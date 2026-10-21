import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, func, or_, update, case
from models.base import async_session
from models.user import User
from models.chat import Chat
from models.report import Report, ReportSnapshot
from models.ban import Ban
from models.mute import Mute
from models.payment import Payment, Product, PremiumSubscription
from models.operations import AdminAudit, BotSetting, Broadcast, AnalyticsEvent, Notification
from models.start import StartEvent
from services.result import ServiceResult
from services.moderation import add_warning, apply_progressive, warning_count
from services.chats import ChatService
from services.matching import MatchingService
from utils.time import kyiv_day_bounds, utcnow
import config

# ban и unban сериализуются в пределах процесса: на Postgres их держит
# FOR UPDATE/advisory lock, на SQLite обе блокировки -- пустышка, и
# интерливинг рвёт инвариант is_banned ⇔ активная строка Ban. Мьютекс
# живёт на event loop (глобальный asyncio.Lock привязывается к первому
# loop и роняет остальные).
_ban_locks: dict = {}


def _ban_mutex():
    loop = asyncio.get_running_loop()
    lock = _ban_locks.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        _ban_locks[id(loop)] = lock
    return lock


# Single source of truth: the panel, the service layer and audience_size all
# derive from this, so a new segment cannot be added in one place only.
BROADCAST_SEGMENTS = ("all", "premium", "inactive", "nopay", "no_language")

# Tunable bot parameters. One catalog drives the /setting validation, the
# panel's editor screens and the quick-preset buttons, so a parameter cannot
# be settable in one place and invisible in another.
SETTING_LIMITS = {
    "search_slow_offer_seconds": (0, 3600), "upsell_chat_count": (1, 100),
    "chat_delay_next": (0, 60), "search_timeout": (30, 3600),
    "premium_search_timeout": (0, 3600), "inactive_chat_ttl": (60, 86400),
    "marketing_enabled": (0, 1), "seasonal_enabled": (0, 1),
    "ad_cooldown_seconds": (0, 86400), "onboarding_nudge_hours": (1, 168), "stars_usd_cents_per_100": (0, 1000000),
    "funnel_min_step_pct": (0, 100),
}

SETTING_TITLES = {
    "chat_delay_next": "Задержка «Дальше», сек",
    "search_timeout": "Таймаут поиска, сек",
    "premium_search_timeout": "Таймаут поиска Premium, сек (0 — как у всех)",
    "inactive_chat_ttl": "Обрыв неактивного диалога, сек",
    "search_slow_offer_seconds": "Оффер «медленный поиск» после, сек",
    "upsell_chat_count": "Апселл после N диалогов",
    "marketing_enabled": "Маркетинговые уведомления (0/1)",
    "seasonal_enabled": "Сезонное событие (0/1)",
    "ad_cooldown_seconds": "Частота показа рекламы, сек",
    "onboarding_nudge_hours": "Напоминание о брошенной регистрации, ч",
    "stars_usd_cents_per_100": "Курс $ за 100 звёзд, центы (0 — не показывать)",
    "funnel_min_step_pct": "Порог просадки шага воронки, % (0 — off)",
}

# Values the code falls back to when the key has never been written.
SETTING_DEFAULTS = {
    "chat_delay_next": 7, "search_timeout": 180, "premium_search_timeout": 180,
    "inactive_chat_ttl": 300, "search_slow_offer_seconds": 20,
    "upsell_chat_count": 3, "marketing_enabled": 1, "seasonal_enabled": 0,
    "ad_cooldown_seconds": 1800, "onboarding_nudge_hours": 3, "stars_usd_cents_per_100": 0,
    "funnel_min_step_pct": 0,
}

SETTING_PRESETS = {
    "chat_delay_next": (0, 3, 7, 15, 30),
    "search_timeout": (60, 120, 180, 300, 600),
    "premium_search_timeout": (0, 60, 120, 180),
    "inactive_chat_ttl": (120, 300, 600, 1800),
    "search_slow_offer_seconds": (10, 20, 30, 60),
    "upsell_chat_count": (1, 3, 5, 10),
    "marketing_enabled": (0, 1),
    "seasonal_enabled": (0, 1),
    "ad_cooldown_seconds": (600, 1800, 3600),
    "onboarding_nudge_hours": (1, 3, 6, 12, 24),
    "stars_usd_cents_per_100": (0, 110, 130, 150),
    "funnel_min_step_pct": (0, 30, 50, 70),
}

SETTING_KEYS = tuple(SETTING_LIMITS)
SETTING_PAGE_SIZE = 5


def authorize(admin_id):
    if admin_id not in config.ADMIN_IDS:
        raise PermissionError("Admin access required")


def audit(s, admin, action, target_type, target_id, before=None, after=None,request_key=None):
    s.add(AdminAudit(admin_id=admin, action=action, target_type=target_type, target_id=str(target_id), before_json=before, after_json=after,request_key=request_key))


async def worker_heartbeat():
    """Возраст последнего такта воркера в секундах, либо None -- данных нет.

    None не значит «воркер мёртв»: файл лежит в /tmp контейнера с ботом, а
    админка живёт в соседнем, где его не существует никогда. maintenance.run()
    держит зеркало в Redis именно для этого, и только когда обе ветки молчат,
    панель имеет право показать, что такта не было видно."""
    import time as _time
    from pathlib import Path
    try:
        beat = Path("/tmp/anon-worker-heartbeat")
        if beat.exists():
            return round(_time.time() - float(beat.read_text().strip() or 0), 1)
    except (OSError, ValueError):
        pass
    try:
        from services.chat_manager import get_redis
        raw = await (await get_redis()).get("worker:heartbeat")
        if raw:
            return round(_time.time() - float(raw), 1)
    except Exception:  # noqa: S110 -- a dead Redis or a malformed heartbeat
        pass           # must report "unknown", not take the endpoint down
    return None


class StatsService:
    async def retention(self,s,days):
        history=await s.scalar(select(func.min(AnalyticsEvent.created_at)).where(AnalyticsEvent.event=="daily_active"))
        if not history:
            return None
        cutoff=utcnow().replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days)
        cohort=(User.created_at>=history,User.created_at<cutoff)
        total=await s.scalar(select(func.count(User.id)).where(*cohort))
        if not total:
            return None
        target=func.date(User.created_at)+days if s.bind.dialect.name=="postgresql" else func.date(User.created_at,f"+{days} day")
        returned=await s.scalar(select(func.count(func.distinct(User.id))).join(AnalyticsEvent,AnalyticsEvent.user_id==User.telegram_id).where(*cohort,AnalyticsEvent.event=="daily_active",func.date(AnalyticsEvent.created_at)==target))
        return round(100*returned/total,1)

    async def onboarding_funnel(self, days=1):
        """Where signups stop, per cohort of users created in the period.

        Derived from the users table rather than from analytics events, so it
        is exact and covers cohorts registered before the funnel existed. Each
        stage is a column the step can only fill in order, so the counts are
        monotonic by construction.
        """
        cutoff = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=days-1)
        cohort = (User.created_at >= cutoff, User.deleted_at.is_(None))
        stages = (("Запустили /start", None),
                  ("Нажали «Начать»", User.onboarding_step != "WELCOME"),
                  ("Указали пол", User.gender.in_(["male", "female"])),
                  ("Указали возраст", User.age.isnot(None)),
                  ("Приняли правила", User.onboarding_completed.is_(True)))
        async with async_session() as s:
            rows = []
            for label, condition in stages:
                where = cohort if condition is None else cohort+(condition,)
                rows.append((label, await s.scalar(select(func.count(User.id)).where(*where))))
            # The age gate is the one refusal the users table cannot show:
            # a rejected answer leaves no column behind.
            rejected = await s.scalar(select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
                AnalyticsEvent.event == "onboarding_age_rejected", AnalyticsEvent.created_at >= cutoff))
            # Same dialect split as retention(): SQLite has no interval maths.
            elapsed = (func.extract("epoch", User.rules_accepted_at-User.created_at)
                       if s.bind.dialect.name == "postgresql" else
                       (func.julianday(User.rules_accepted_at)-func.julianday(User.created_at))*86400)
            minutes = await s.scalar(select(func.avg(elapsed)).where(
                *cohort, User.onboarding_completed.is_(True), User.rules_accepted_at.isnot(None)))
            done = func.sum(case((User.onboarding_completed.is_(True), 1), else_=0))
            daily = (await s.execute(select(func.date(User.created_at), func.count(User.id), done)
                .where(*cohort).group_by(func.date(User.created_at))
                .order_by(func.date(User.created_at)))).all()
        return dict(stages=rows, age_rejected=rejected,
                    avg_minutes=round(minutes/60, 1) if minutes is not None else None,
                    daily=[(str(day), total, done) for day, total, done in daily])

    async def growth(self, days=1):
        """Acquisition funnel: broadcasts to returns to chats, plus the
        re-engagement nudges and the /start source tags.

        Rolling window instead of calendar days like the other stats -- this
        reads a campaign that shipped an hour ago, not a daily card. Return
        and chat rates are measured from each broadcast's own send time, so a
        seven-day view does not credit old sends with new returns."""
        since = utcnow()-timedelta(days=days)
        async with async_session() as s:
            jobs = []
            for bc in (await s.scalars(select(Broadcast).where(
                    Broadcast.created_at >= since).order_by(Broadcast.id))).all():
                delivered = (Notification.broadcast_id == bc.id, Notification.status == "sent")
                returned = await s.scalar(select(func.count(func.distinct(Notification.user_id)))
                    .join(User, User.telegram_id == Notification.user_id)
                    .where(*delivered, User.last_online > bc.created_at))
                chatted = await s.scalar(select(func.count(func.distinct(Notification.user_id)))
                    .join(Chat, or_(Chat.user1_id == Notification.user_id,
                                    Chat.user2_id == Notification.user_id))
                    .where(*delivered, Chat.started_at > bc.created_at))
                queued = await s.scalar(select(func.count(Notification.id)).where(
                    Notification.broadcast_id == bc.id,
                    Notification.status.in_(["queued", "sending"])))
                jobs.append(dict(id=bc.id, created_at=bc.created_at, sent=bc.sent,
                                 failed=bc.failed, queued=queued or 0,
                                 returned=returned or 0, chatted=chatted or 0))
            nudges = dict((await s.execute(select(Notification.type, func.count(Notification.id)).where(
                Notification.status == "sent", Notification.sent_at >= since,
                Notification.type.in_(["inactive", "prime_time", "onboarding_nudge"]))
                .group_by(Notification.type))).all())
            # The source tag is the third colon-separated part of the key;
            # parsed here instead of split_part() so the query also runs on
            # the SQLite test harness.
            sources = {}
            for key in (await s.scalars(select(AnalyticsEvent.dedupe_key).where(
                    AnalyticsEvent.event == "start_source",
                    AnalyticsEvent.created_at >= since))).all():
                parts = key.split(":")
                tag = parts[2] if len(parts) > 2 else "none"
                sources[tag] = sources.get(tag, 0)+1
            new = await s.scalar(select(func.count(User.id)).where(User.created_at >= since))
            completed = await s.scalar(select(func.count(User.id)).where(
                User.created_at >= since, User.onboarding_completed.is_(True)))
            chats = await s.scalar(select(func.count(Chat.id)).where(Chat.started_at >= since))
        return dict(days=days, jobs=jobs, nudges=nudges, sources=sources,
                    new=new, completed=completed, chats=chats)

    async def overview(self, days=1):
        # Сутки -- по календарю Киева: «новых сегодня» в панели и в карточке
        # первого запуска должны означать один и тот же день.
        day_start, day_end = kyiv_day_bounds()
        cutoff=day_start-timedelta(days=days-1)
        week_ago=utcnow()-timedelta(days=7)
        two_weeks_ago=utcnow()-timedelta(days=14)
        async with async_session() as s:
            users=await s.scalar(select(func.count(User.id)))
            new=await s.scalar(select(func.count(User.id)).where(User.created_at>=cutoff))
            new_7d=await s.scalar(select(func.count(User.id)).where(User.created_at>=week_ago))
            new_prev_7d=await s.scalar(select(func.count(User.id)).where(
                User.created_at>=two_weeks_ago,User.created_at<week_ago))
            starts=await s.scalar(select(func.count(StartEvent.id)))
            starts_today=await s.scalar(select(func.count(StartEvent.id)).where(
                StartEvent.created_at>=day_start,StartEvent.created_at<day_end))
            chats=await s.scalar(select(func.count(Chat.id)).where(Chat.status=="ended",Chat.ended_at>=cutoff))
            duration=await s.scalar(select(func.avg(Chat.duration_seconds)).where(Chat.status=="ended",Chat.ended_at>=cutoff))
            stars=await s.scalar(select(func.coalesce(func.sum(Payment.stars_amount),0)).where(Payment.status=="paid",Payment.paid_at>=cutoff))
            premium=await s.scalar(select(func.count(User.id)).where(User.premium_expires>utcnow()))
            products=(await s.execute(select(Payment.product,func.sum(Payment.stars_amount)).where(Payment.status=="paid",Payment.paid_at>=cutoff).group_by(Payment.product))).all()
            product_orders=(await s.execute(select(Payment.product,func.count(Payment.id)).where(
                Payment.status=="paid",Payment.paid_at>=cutoff).group_by(Payment.product))).all()
            completed=await s.scalar(select(func.count(User.id)).where(
                User.created_at>=cutoff, User.onboarding_completed.is_(True)))
            searchers=await s.scalar(select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
                AnalyticsEvent.created_at>=cutoff, AnalyticsEvent.event=="search_started"))
            matched=await s.scalar(select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
                AnalyticsEvent.created_at>=cutoff, AnalyticsEvent.event=="chat_started"))
            buyers=await s.scalar(select(func.count(func.distinct(Payment.user_id))).join(
                User, User.telegram_id==Payment.user_id).where(
                Payment.status=="paid", Payment.paid_at>=cutoff, User.created_at>=cutoff))
            funnel=dict((await s.execute(select(AnalyticsEvent.event,func.count(func.distinct(AnalyticsEvent.user_id))).where(AnalyticsEvent.created_at>=cutoff).group_by(AnalyticsEvent.event))).all())
            rating=await s.scalar(select(func.avg(User.positive_rating_pct)).where(User.likes_received+User.dislikes_received>0))
            d1,d7=await self.retention(s,1),await self.retention(s,7)
        orders=dict(product_orders)
        total_orders=sum(orders.values())
        product_shares={key: round(100*value/total_orders, 1) for key, value in orders.items()} if total_orders else {}
        growth_7d_pct=round(100*(new_7d-new_prev_7d)/new_prev_7d,1) if new_prev_7d else None
        from services.settings import bot_value
        # Undefined by default: showing a $ estimate off a made-up rate is the
        # kind of fake monetization data rule 13 forbids.
        usd_rate=await bot_value("stars_usd_cents_per_100",0)
        stars_usd=round(stars*usd_rate/10000,2) if usd_rate else None
        return dict(users=users,new=new,starts=starts,starts_today=starts_today,
            chats=chats,avg_seconds=round(duration or 0),stars=stars,
            stars_usd=stars_usd,premium=premium,products=dict(products),product_orders=orders,
            product_shares=product_shares,new_7d=new_7d,growth_7d_pct=growth_7d_pct,
            funnel=funnel,rating=round(rating,1) if rating is not None else None,
            retention_d1=d1,retention_d7=d7, onboarding_completed=completed,
            onboarding_conversion=round(100*completed/new,1) if new else None,
            searchers=searchers,matched=matched,
            search_conversion=round(100*matched/searchers,1) if searchers else None,
            buyers=buyers,purchase_conversion=round(100*buyers/new,1) if new else None)


class AdminService:
    async def seasonal(self,admin,code,starts_at,ends_at,discount):
        authorize(admin)
        from datetime import datetime
        import re
        try:
            start,end=datetime.fromisoformat(starts_at),datetime.fromisoformat(ends_at)
            valid=start.tzinfo is not None and end.tzinfo is not None and start<end and end>utcnow()
        except (ValueError,TypeError):
            valid=False
        if not valid or not re.fullmatch(r"[a-z0-9_]{1,32}",code) or type(discount) is not int or not 1<=discount<=90:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            item=await s.get(BotSetting,"seasonal_event",with_for_update=True)
            previous=item.value_json if item else None
            if not item:
                item=BotSetting(key="seasonal_event")
                s.add(item)
            item.value_json={"code":code,"starts_at":start.isoformat(),"ends_at":end.isoformat(),"discount":discount}
            item.updated_at,item.updated_by=utcnow(),admin
            audit(s,admin,"seasonal","setting","seasonal_event",previous,item.value_json)
        return ServiceResult(True,"SAVED")

    async def reports(self, admin, offset=0):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(Report).where(Report.status=="pending").order_by(Report.id).offset(offset).limit(10))).all())

    async def reports_against(self, admin, uid):
        authorize(admin)
        async with async_session() as s:
            return await s.scalar(select(func.count(Report.id)).where(Report.reported_id == uid))

    async def report_details(self,admin,report):
        authorize(admin)
        async with async_session() as s:
            users=(await s.scalars(select(User).where(User.telegram_id.in_([report.reporter_id,report.reported_id])))).all()
            names={u.telegram_id:u.anon_id for u in users}
            previous=await s.scalar(select(func.count(Report.id)).where(Report.reported_id==report.reported_id,Report.id!=report.id))
            bans=await s.scalar(select(func.count(Ban.id)).where(Ban.user_id==report.reported_id))
            mutes=await s.scalar(select(func.count(Mute.id)).where(Mute.user_id==report.reported_id))
            return names,previous,bans,mutes

    async def warnings_against(self, admin, uid):
        authorize(admin)
        async with async_session() as s:
            return await warning_count(s, uid)

    async def snapshot_count(self, admin, report_id):
        authorize(admin)
        async with async_session() as s:
            return int(await s.scalar(select(func.count(ReportSnapshot.id)).where(
                ReportSnapshot.report_id == report_id,
                ReportSnapshot.file_id.is_not(None))) or 0)

    async def snapshots(self, admin, report_id):
        """Media-only: the report card already renders the text excerpts."""
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(ReportSnapshot).where(
                ReportSnapshot.report_id == report_id,
                ReportSnapshot.file_id.is_not(None)).order_by(ReportSnapshot.id))).all())

    async def resolve(self, admin, report_id, action):
        authorize(admin)
        if action not in {"ban1", "banf", "mute1", "mute24", "mute7", "warn", "dismiss"}:
            return ServiceResult(False,"INVALID")
        if not 0 < report_id <= 2**63 - 1:
            # int64: шире -- s.get() кидал OverflowError посреди транзакции (M1).
            return ServiceResult(False,"NOT_FOUND")
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            report=await s.get(Report,report_id,with_for_update=True)
            if not report or report.status!="pending":
                return ServiceResult(False,"ALREADY_RESOLVED")
            report.status="dismissed" if action=="dismiss" else "resolved"
            report.resolved_at,report.resolved_by=utcnow(),admin
            penalty=None
            if action!="dismiss":
                # Every confirmed report produces exactly one warning; the
                # explicit action is applied first, then the thresholds may
                # upgrade it (a fifth warning turns a 1h mute into a ban).
                await add_warning(s, report.reported_id, report.reason,
                                  issued_by=admin, report_id=report.id)
                if action.startswith("mute"):
                    hours={"mute1":1,"mute24":24,"mute7":168}[action]
                    await self._mute(s,admin,report.reported_id,report.reason,hours,report.id)
                elif action.startswith("ban"):
                    await self._ban(s,admin,report.reported_id,report.reason,24 if action=="ban1" else None,report.id)
                penalty=await apply_progressive(s, report.reported_id)
            audit(s,admin,action,"report",report_id,{"status":"pending"},{"status":report.status})
            uid=report.reported_id
        if action.startswith("mute") or penalty=="mute":
            await MatchingService().cancel(uid)
            await ChatService().end_chat(uid,reason="mute")
        elif action.startswith("ban") or penalty=="ban":
            await MatchingService().cancel(uid)
            await ChatService().end_chat(uid,reason="ban")
        return ServiceResult(True,"RESOLVED",{"penalty":penalty})

    async def _ban(self,s,admin,uid,reason,hours,report_id=None):
        user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
        if not user:
            raise ValueError("User not found")
        expires=utcnow()+timedelta(hours=hours) if hours else None
        await s.execute(update(Ban).where(Ban.user_id==uid, Ban.is_active.is_(True)).values(is_active=False))
        user.is_banned,user.ban_expires=True,expires
        record=Ban(user_id=uid,reason=reason[:64],duration_hours=hours,expires_at=expires,banned_by=admin,report_id=report_id)
        s.add(record)
        await s.flush()
        from services.notifications import enqueue
        until=f"до {expires:%d.%m.%Y %H:%M} UTC" if expires else "бессрочно"
        await enqueue(s,uid,"ban",f"ban:{record.id}",f"⛔ Ты заблокирован {until}. Причина: {reason[:500]}")

    async def _mute(self,s,admin,uid,reason,hours,report_id=None):
        user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
        if not user:
            raise ValueError("User not found")
        expires=utcnow()+timedelta(hours=hours) if hours else None
        await s.execute(update(Mute).where(Mute.user_id==uid, Mute.is_active.is_(True)).values(is_active=False))
        user.is_muted,user.mute_expires=True,expires
        record=Mute(user_id=uid,reason=reason[:64],duration_hours=hours,expires_at=expires,muted_by=admin,report_id=report_id)
        s.add(record)
        await s.flush()
        from services.notifications import enqueue
        until=f"до {expires:%d.%m.%Y %H:%M} UTC" if expires else "бессрочно"
        await enqueue(s,uid,"mute",f"mute:{record.id}",f"🔇 Мут {until}: писать и искать собеседника нельзя. Причина: {reason[:500]}")

    async def start_events_count(self, telegram_id):
        """Сколько событий запуска реально записано -- для сверки со
        start_count в карточке человека: расхождение значит, что счётчик
        правили мимо регистрации."""
        async with async_session() as s:
            return await s.scalar(select(func.count(StartEvent.id)).where(
                StartEvent.telegram_user_id == telegram_id)) or 0

    async def find_user(self,admin,value):
        authorize(admin)
        value = value.strip()
        if not value:
            return None
        anon_id = "#" + value.lstrip("#")
        telegram_id = int(value) if value.isascii() and value.isdigit() and len(value) <= 19 else -1
        if telegram_id > 9223372036854775807:
            telegram_id = -1
        async with async_session() as s:
            q=select(User).where(or_(func.lower(User.anon_id)==anon_id.lower(),User.telegram_id==telegram_id))
            return await s.scalar(q)

    async def ban(self,admin,uid,reason,hours=None):
        authorize(admin)
        if not reason or (hours is not None and hours != 0 and not 1<=hours<=87600):
            return ServiceResult(False,"INVALID")
        async with _ban_mutex():
            async with async_session.begin() as s:
                from repositories.chats import lock_matching
                await lock_matching(s)
                await self._ban(s,admin,uid,reason,hours)
                audit(s,admin,"ban","user",uid,after={"reason":reason,"hours":hours})
        await MatchingService().cancel(uid)
        await ChatService().end_chat(uid,reason="ban")
        logging.getLogger(__name__).info("user_banned", extra={"user_id": uid})
        return ServiceResult(True,"BANNED")

    async def mute(self,admin,uid,reason,hours=None):
        authorize(admin)
        if not reason or (hours is not None and hours != 0 and not 1<=hours<=87600):
            return ServiceResult(False,"INVALID")
        async with _ban_mutex():
            async with async_session.begin() as s:
                from repositories.chats import lock_matching
                await lock_matching(s)
                await self._mute(s,admin,uid,reason,hours)
                audit(s,admin,"mute","user",uid,after={"reason":reason,"hours":hours})
        await MatchingService().cancel(uid)
        await ChatService().end_chat(uid,reason="mute")
        logging.getLogger(__name__).info("user_muted", extra={"user_id": uid})
        return ServiceResult(True,"MUTED")

    async def unban(self,admin,uid):
        authorize(admin)
        async with _ban_mutex():
            async with async_session.begin() as s:
                from repositories.chats import lock_matching
                await lock_matching(s)
                user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
                if not user:
                    return ServiceResult(False,"NOT_FOUND")
                before={"is_banned":user.is_banned}
                user.is_banned,user.ban_expires=False,None
                await s.execute(update(Ban).where(Ban.user_id==uid, Ban.is_active.is_(True)).values(is_active=False))
                if before["is_banned"]:
                    from services.notifications import enqueue
                    await enqueue(s,uid,"unban",f"unban:{uid}:{utcnow().isoformat()}","<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Блокировка снята, добро пожаловать обратно!", "menu")
                audit(s,admin,"unban","user",uid,before,{"is_banned":False})
        return ServiceResult(True,"UNBANNED")

    async def unmute(self,admin,uid):
        authorize(admin)
        async with _ban_mutex():
            async with async_session.begin() as s:
                from repositories.chats import lock_matching
                await lock_matching(s)
                user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
                if not user:
                    return ServiceResult(False,"NOT_FOUND")
                before={"is_muted":user.is_muted}
                user.is_muted,user.mute_expires=False,None
                await s.execute(update(Mute).where(Mute.user_id==uid, Mute.is_active.is_(True)).values(is_active=False))
                if before["is_muted"]:
                    from services.notifications import enqueue
                    await enqueue(s,uid,"unmute",f"unmute:{uid}:{utcnow().isoformat()}","🔊 Мут снят — можно снова писать и искать собеседника.","search:start")
                audit(s,admin,"unmute","user",uid,before,{"is_muted":False})
        return ServiceResult(True,"UNMUTED")

    async def bans(self,admin):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(User).where(User.is_banned.is_(True),or_(User.ban_expires.is_(None),User.ban_expires>utcnow())).limit(50))).all())

    async def grant(self,admin,uid,days,reason,request_key=None):
        authorize(admin)
        if not 1<=days<=3650 or not reason:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
            if not user:
                return ServiceResult(False,"NOT_FOUND")
            if request_key and await s.scalar(select(AdminAudit.id).where(AdminAudit.request_key==request_key)):
                return ServiceResult(False,"ALREADY_GRANTED")
            old=user.premium_expires
            user.premium_expires=max(utcnow(),old or utcnow())+timedelta(days=days)
            user.is_premium=True
            s.add(PremiumSubscription(user_id=uid,plan="admin",stars_paid=0,expires_at=user.premium_expires))
            audit(s,admin,"grant_premium","user",uid,{"expires":old.isoformat() if old else None},{"expires":user.premium_expires.isoformat(),"reason":reason},request_key=request_key)
        return ServiceResult(True,"GRANTED")

    async def price(self,admin,code,stars):
        authorize(admin)
        if not 1<=stars<=100000:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            p=await s.get(Product,code,with_for_update=True)
            if not p:
                return ServiceResult(False,"NOT_FOUND")
            before=p.price_stars
            p.price_stars,p.updated_at=stars,utcnow()
            audit(s,admin,"price","product",code,{"price":before},{"price":stars})
        return ServiceResult(True,"SAVED")

    async def setting(self,admin,key,value):
        authorize(admin)
        limits=SETTING_LIMITS
        if not isinstance(key, str) or type(value) is not int or key not in limits or not limits[key][0]<=value<=limits[key][1]:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            setting=await s.get(BotSetting,key,with_for_update=True)
            before=setting.value_json if setting else None
            if not setting:
                setting=BotSetting(key=key)
                s.add(setting)
            setting.value_json,setting.updated_at,setting.updated_by={"value":value},utcnow(),admin
            audit(s,admin,"setting","setting",key,before,{"value":value})
        return ServiceResult(True,"SAVED")

    async def broadcast(self,admin,segment,content,kind="text",poll_options=None):
        authorize(admin)
        if segment not in BROADCAST_SEGMENTS or kind not in {"text","poll"}:
            return ServiceResult(False,"INVALID")
        if not isinstance(content, str):
            # len() на int из JSON-тела падал TypeError (M1).
            return ServiceResult(False,"INVALID")
        if kind=="text":
            if not 1<=len(content)<=4000:
                return ServiceResult(False,"INVALID")
        else:
            # Telegram's own send_poll limits: question <=300 chars, 2-10 options, each <=100 chars.
            if not 1<=len(content)<=300:
                return ServiceResult(False,"INVALID")
            if not poll_options or not 2<=len(poll_options)<=10 or any(not 1<=len(o)<=100 for o in poll_options):
                return ServiceResult(False,"INVALID")
        import json
        async with async_session.begin() as s:
            job=Broadcast(admin_id=admin,segment=segment,content=content,kind=kind,
                poll_options=json.dumps(poll_options) if kind=="poll" else None)
            s.add(job)
            await s.flush()
            audit(s,admin,"broadcast_draft","broadcast",job.id,after={"segment":segment,"kind":kind})
        return ServiceResult(True,"DRAFT",{"job":job})

    async def audience_size(self,admin,segment):
        authorize(admin)
        if segment not in BROADCAST_SEGMENTS:
            return 0
        from services.notifications import broadcast_segment
        async with async_session() as s:
            return await s.scalar(broadcast_segment(select(func.count(User.id)),segment)) or 0

    async def confirm_broadcast(self,admin,job_id):
        authorize(admin)
        from services.notifications import broadcast_segment
        async with async_session.begin() as s:
            job=await s.get(Broadcast,job_id,with_for_update=True)
            if not job or job.admin_id!=admin or job.status!="draft":
                return ServiceResult(False,"ALREADY_STARTED")
            # Snapshot of the audience at launch; it is what progress is measured against.
            job.total=await s.scalar(broadcast_segment(select(func.count(User.id)),job.segment)) or 0
            job.status,job.started_at="queued",utcnow()
            audit(s,admin,"broadcast_confirm","broadcast",job.id,after={"status":"queued","total":job.total})
        return ServiceResult(True,"QUEUED",{"job":job})

    async def attach_progress(self,admin,job_id,chat_id,message_id):
        """Bind the admin message that the worker keeps refreshing."""
        authorize(admin)
        async with async_session.begin() as s:
            job=await s.get(Broadcast,job_id,with_for_update=True)
            if not job or job.admin_id!=admin:
                return ServiceResult(False,"NOT_FOUND")
            job.progress_chat_id,job.progress_message_id,job.progress_at=chat_id,message_id,None
        return ServiceResult(True,"ATTACHED")

    async def broadcast_progress(self,admin,job_id):
        authorize(admin)
        from services.notifications import NotificationService
        async with async_session() as s:
            job=await s.get(Broadcast,job_id)
            if not job or job.admin_id!=admin:
                return None
            return job,await NotificationService().broadcast_counts(s,job.id)

    async def send_message_to_user(self,admin,target_id,text,request_key=None):
        authorize(admin)
        if not text or len(text)>4000:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==target_id).with_for_update())
            if not user:
                return ServiceResult(False,"NOT_FOUND")
            if request_key and await s.scalar(select(AdminAudit.id).where(AdminAudit.request_key==request_key)):
                return ServiceResult(False,"ALREADY_SENT")
            from services.notifications import enqueue
            await enqueue(s,target_id,"admin_message",f"admin_msg:{request_key}",text,None)
            audit(s,admin,"send_message","user",target_id,after={"text":text[:200]},request_key=request_key)
        return ServiceResult(True,"SENT",{"user":user})

    async def broadcast_status(self,admin,job_id=None):
        authorize(admin)
        async with async_session() as s:
            if job_id:
                job=await s.get(Broadcast,job_id)
                if not job or job.admin_id!=admin:
                    return None
                return job
            jobs=list((await s.scalars(select(Broadcast).where(Broadcast.admin_id==admin).order_by(Broadcast.id.desc()).limit(10))).all())
            return jobs

    async def cancel_broadcast(self,admin,job_id):
        """Stop a broadcast that has not finished yet.

        Only queued rows are dropped; a message already handed to Telegram
        cannot be recalled, so the card keeps the sent/failed counters honest
        instead of pretending the wave never went out.
        """
        authorize(admin)
        async with async_session.begin() as s:
            job=await s.get(Broadcast,job_id,with_for_update=True)
            if not job or job.admin_id!=admin:
                return ServiceResult(False,"NOT_FOUND")
            if job.status in {"completed","cancelled"}:
                return ServiceResult(False,"ALREADY_FINISHED")
            job.status,job.finished_at="cancelled",utcnow()
            dropped=await s.execute(update(Notification).where(
                Notification.broadcast_id==job.id,
                Notification.status.in_(["queued","sending"])).values(status="skipped",last_error="cancelled"))
            audit(s,admin,"broadcast_cancel","broadcast",job.id,after={"status":"cancelled"})
        return ServiceResult(True,"CANCELLED",{"job":job,"dropped":dropped.rowcount or 0})

    async def settings_all(self,admin):
        """Current value of every catalog key, falling back to the default."""
        authorize(admin)
        async with async_session() as s:
            rows={row.key: row.value_json.get("value") for row in
                  (await s.scalars(select(BotSetting).where(BotSetting.key.in_(SETTING_KEYS)))).all()}
        return {key: rows.get(key, SETTING_DEFAULTS.get(key)) for key in SETTING_KEYS}

    async def audit_log(self,admin,offset=0,limit=10):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(AdminAudit).order_by(AdminAudit.id.desc())
                                         .offset(offset).limit(limit))).all())

    async def top_spenders(self,admin,limit=10):
        """Leaderboard by real Stars: paid live rows only, ties by recency."""
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(User).where(User.total_stars_spent>0,
                                                            User.deleted_at.is_(None))
                .order_by(User.total_stars_spent.desc(),User.telegram_id.desc()).limit(limit))).all())

    async def recent_users(self,admin,limit=10):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(User).where(User.deleted_at.is_(None))
                .order_by(User.created_at.desc()).limit(limit))).all())

    async def system_status(self,admin):
        """Live health for the panel: worker heartbeat, DB, Redis, outbox.

        Mirrors utils/healthcheck.check() but never raises and never closes
        shared connections -- the panel is a read, not a probe.
        """
        authorize(admin)
        state={"heartbeat_age":None,"db":False,"redis":False,"queued":0,"sending":0,
               "broadcasts_active":0,"reports_pending":0,"users":0,"online":"—"}
        state["heartbeat_age"]=await worker_heartbeat()
        try:
            async with async_session() as s:
                state["db"]=True
                state["users"]=await s.scalar(select(func.count(User.id))) or 0
                state["queued"]=await s.scalar(select(func.count(Notification.id)).where(
                    Notification.status.in_(["queued","sending"]))) or 0
                state["broadcasts_active"]=await s.scalar(select(func.count(Broadcast.id)).where(
                    Broadcast.status.in_(["queued","sending"]))) or 0
                state["reports_pending"]=await s.scalar(select(func.count(Report.id)).where(
                    Report.status=="pending")) or 0
        except Exception:
            state["db"]=False
        try:
            from services.chat_manager import get_redis
            await (await get_redis()).ping()
            state["redis"]=True
        except Exception:
            state["redis"]=False
        from services.chat_manager import get_online_count
        state["online"]=await get_online_count()
        return state
