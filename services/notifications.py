"""Durable outbox. Telegram transport is owned by workers, not services."""
import json
import config
from datetime import timedelta
from sqlalchemy import select, func, or_, update
from models.base import async_session
from models.operations import Notification, Broadcast, Offer, PollVote
from models.payment import Payment
from models.user import User
from models.chat import Chat, ActiveParticipant
from utils.time import utcnow
from services.onboarding import banned

UNBAN_TEXT = {"ru": "✅ Срок блокировки истёк. Добро пожаловать обратно!",
              "en": "✅ Your ban has expired. Welcome back!",
              "uk": "✅ Термін блокування минув. Ласкаво просимо назад!"}
ONBOARDING_NUDGE_TEXTS = [
    {"ru": "⏳ Ты начал — но не закончил. Одна кнопка, и случайный собеседник твой. Чего ждёшь?",
     "en": "⏳ You started but didn't finish. One button and a random match is yours. What's stopping you?",
     "uk": "⏳ Ти почав — але не закінчив. Одна кнопка, і випадковий співрозмовник твій. Чого чекаєш?"},
    {"ru": "🕵️ Между тобой и первым анонимным диалогом — один шаг. Мы оставили его за тобой.",
     "en": "🕵️ One step separates you from your first anonymous chat. We left it to you.",
     "uk": "🕵️ Між тобою і першим анонімним діалогом — один крок. Ми залишили його за тобою."},
    {"ru": "👀 Другие уже общаются здесь без имён и анкет. Ты застрял на старте — поставь точку в регистрации.",
     "en": "Others are already talking here with no names and no profiles. You stalled at the start — finish the signup.",
     "uk": "👀 Інші вже спілкуються тут без імен і анкет. Ти застряг на старті — заверши реєстрацію."},
]
PREMIUM_EXPIRY_TEXT = {"ru": "⏰ Твой Premium заканчивается завтра. Продлить?",
                       "en": "⏰ Your Premium ends tomorrow. Renew?",
                       "uk": "⏰ Твій Premium закінчується завтра. Продовжити?"}
# Two pools for the return nudge: "hot" quotes how many chats happened while
# the user was away -- truthful only when that number is above zero. A dead
# "{count} новых диалогов" when the answer is 0 advertises an empty room and
# kills the return instead of provoking it, so count==0 gets its own copy
# that never claims traffic.
INACTIVE_TEXTS_HOT = [
    {"ru": "🔥 {count} новых диалогов — и ни один не твой. Очередь открыта, собеседники на месте. Не дай им заскучать 👇",
     "en": "🔥 {count} new chats happened — and none of them was yours. The queue is open, matches are waiting. Don't leave them hanging 👇",
     "uk": "🔥 {count} нових діалогів — і жоден не твій. Черга відкрита, співрозмовники на місці. Не дай їм заскучати 👇"},
    {"ru": "🕵️ Среди {count} диалогов за это время, может, был тот, с кем тебе было бы по нраву. Или нет. Проверь сам 👀",
     "en": "🕵️ Of the {count} chats since you left, maybe one had your kind of person in it. Or maybe not. See for yourself 👀",
     "uk": "🕵️ Можливо, серед {count} діалогів за цей час був той, з ким тобі було б до вподоби. А може, й ні. Перевір сам 👀"},
    {"ru": "👀 {count} разговоров случилось без тебя. Ничего личного — но каждый из них мог быть твоим.",
     "en": "👀 {count} conversations happened without you. Nothing personal — each one could have been yours.",
     "uk": "👀 {count} розмов відбулося без тебе. Нічого особистого — кожна з них могла бути твоєю."},
    {"ru": "💬 О {count} диалогах здесь говорят так, как не говорят при своих. Ты ещё не внутри?",
     "en": "💬 {count} chats here went the way talks go only among strangers. Not inside yet?",
     "uk": "💬 Про {count} діалогів тут говорять так, як не говорять при своїх. Ти ще не всередині?"},
]
INACTIVE_TEXTS_COLD = [
    {"ru": "👀 Ты же ещё не сказал последнего слова. Случайный собеседник уже готов — просто ждёт.",
     "en": "👀 You haven't said your last word yet. A random match is ready and waiting.",
     "uk": "👀 Ти ж ще не сказав останнього слова. Випадковий співрозмовник уже чекає."},
    {"ru": "🕵️ Твой собеседник где-то есть. Он пока не знает, что ты ещё не пришёл. Исправь.",
     "en": "🕵️ Your match is out there somewhere. He doesn't know yet that you haven't arrived. Fix that.",
     "uk": "🕵️ Твій співрозмовник десь є. Він ще не знає, що ти не прийшов. Виправ це."},
    {"ru": "⏳ Анонимность ждёт: ни имени, ни анкет, ни последствий. Только ты и собеседник. Загляни 👇",
     "en": "⏳ Anonymity is waiting: no names, no profiles, no consequences. Just you and a match. Drop in 👇",
     "uk": "⏳ Анонімність чекає: ні імен, ні анкет, ні наслідків. Лише ти і співрозмовник. Заглянь 👇"},
]


def _rotate(pool, *seeds):
    """Deterministic variant per user (and day, if passed): a restart must
    not reshuffle what was already sent, while a changing salt keeps
    tomorrow's copy different from today's."""
    import zlib
    return pool[zlib.crc32("|".join(str(s) for s in seeds).encode()) % len(pool)]


def _lang(user):
    return getattr(user, "language", None) or "ru"


def _tr(texts, user):
    return texts.get(_lang(user), texts["ru"])


def broadcast_segment(query, segment, now=None):
    """Single definition of an audience; counting and expanding must not drift."""
    now = now or utcnow()
    query = query.where(User.deleted_at.is_(None), User.bot_blocked_at.is_(None))
    if segment == "premium":
        return query.where(User.premium_expires > now)
    if segment == "inactive":
        return query.where(User.last_online < now-timedelta(days=7))
    if segment == "nopay":
        return query.where(User.total_stars_spent == 0)
    return query


async def enqueue(s, user_id, kind, key, content, callback=None, marketing=False, broadcast_id=None):
    # A plain check-then-insert races under concurrency against dedupe_key's
    # UNIQUE constraint and raises IntegrityError, aborting whatever else the
    # caller's transaction was doing. schedule()'s bulk insert below already
    # uses on_conflict_do_nothing for the same reason; do the same here.
    if s.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    await s.execute(insert(Notification).values(user_id=user_id, type=kind, dedupe_key=key, text=content,
        callback_data=callback, marketing=marketing, broadcast_id=broadcast_id).on_conflict_do_nothing(
        index_elements=["dedupe_key"]))


class NotificationService:
    async def broadcast_poll(self, broadcast_id):
        """(options, question) for a poll-kind broadcast, or None for a plain
        text one / missing job -- lets the worker pick send_poll vs send_message
        without loading the whole Broadcast row into notification dispatch."""
        async with async_session() as s:
            job = await s.get(Broadcast, broadcast_id)
            if not job or job.kind != "poll":
                return None
            return json.loads(job.poll_options), job.content

    async def record_poll(self, poll_id, broadcast_id, option_count):
        """Register a just-sent recipient poll so a later Poll update (see
        handlers/admin.py's @admin_router.poll()) has somewhere to land."""
        async with async_session.begin() as s:
            s.add(PollVote(poll_id=poll_id, broadcast_id=broadcast_id,
                counts=json.dumps([0]*option_count), updated_at=utcnow()))

    async def update_poll(self, poll_id, counts):
        """Store Telegram's latest per-option vote snapshot for one recipient's
        poll. Silently a no-op for poll_ids we never sent (nothing to update)."""
        async with async_session.begin() as s:
            row = await s.get(PollVote, poll_id, with_for_update=True)
            if row:
                row.counts, row.updated_at = json.dumps(counts), utcnow()

    async def poll_totals(self, broadcast_id):
        """Sum every recipient's snapshot into one campaign-wide tally, or
        None if no poll has reported in yet."""
        async with async_session() as s:
            rows = (await s.scalars(select(PollVote.counts).where(
                PollVote.broadcast_id == broadcast_id))).all()
            if not rows:
                return None
            totals = None
            for raw in rows:
                counts = json.loads(raw)
                totals = counts if totals is None else [a+b for a, b in zip(totals, counts)]
            return totals

    async def schedule(self):
        from services.settings import bot_value
        from models.ban import Ban
        from sqlalchemy import text
        marketing_enabled = await bot_value("marketing_enabled", 1)
        nudge_after = timedelta(hours=await bot_value("onboarding_nudge_hours", 3))
        now = utcnow()
        cursor = 0
        while True:
            async with async_session.begin() as s:
                # Independent of the matching lock; each transaction is bounded.
                if s.bind.dialect.name == "postgresql":
                    await s.execute(text("SELECT pg_advisory_xact_lock(8873771497)"))
                users = (await s.scalars(select(User).where(User.id > cursor).order_by(User.id)
                                        .limit(100).with_for_update())).all()
                if not users:
                    break
                cursor = users[-1].id
                ids = [u.telegram_id for u in users]
                recent = set((await s.scalars(select(Notification.user_id).where(
                    Notification.user_id.in_(ids), Notification.marketing.is_(True),
                    Notification.scheduled_at > now-timedelta(hours=24)).distinct())).all())
                # One grouped query for all inactive users in this batch.
                counts = dict((await s.execute(select(User.telegram_id, func.count(Chat.id))
                    .outerjoin(Chat, (Chat.started_at > User.last_online) & (Chat.status == "ended"))
                    .where(User.telegram_id.in_(ids), User.last_online < now-timedelta(days=3))
                    .group_by(User.telegram_id))).all())
                notices = []
                def notice(user, kind, key, content, callback=None, marketing=False):
                    notices.append(dict(user_id=user.telegram_id, type=kind, dedupe_key=key,
                        text=content, callback_data=callback, marketing=marketing))
                for user in users:
                    if user.is_banned and user.ban_expires and user.ban_expires <= now:
                        notice(user, "unban", f"unban_expired:{user.telegram_id}:{user.ban_expires.isoformat()}",
                               _tr(UNBAN_TEXT, user), "menu")
                        user.is_banned = False
                    if user.is_premium and user.premium_expires and user.premium_expires <= now:
                        user.is_premium = False
                    if user.deleted_at or user.bot_blocked_at or banned(user):
                        continue
                    if not user.onboarding_completed:
                        # The people who tapped /start and stopped are the single
                        # largest lost group; nudge each of them exactly once.
                        if (nudge_after <= now-user.created_at <= timedelta(days=7)
                                and marketing_enabled
                                and json.loads(user.notification_settings or "{}").get("marketing", True)):
                            notice(user, "onboarding_nudge", f"onboarding_nudge:{user.telegram_id}",
                                   _tr(_rotate(ONBOARDING_NUDGE_TEXTS, user.telegram_id), user), "menu", True)
                        continue
                    if user.premium_expires and now < user.premium_expires <= now+timedelta(days=1):
                        notice(user, "premium_expiry", f"expiry:{user.telegram_id}:{user.premium_expires.isoformat()}",
                               _tr(PREMIUM_EXPIRY_TEXT, user), "premium")
                    if (marketing_enabled and json.loads(user.notification_settings or "{}").get("marketing", True)
                            and user.telegram_id not in recent and user.telegram_id in counts):
                        fresh = counts[user.telegram_id]
                        # Zero fresh chats gets copy that never claims traffic:
                        # advertising an empty room sends the opposite signal.
                        pool = INACTIVE_TEXTS_HOT if fresh else INACTIVE_TEXTS_COLD
                        notice(user, "inactive", f"inactive:{user.telegram_id}:{now.date()}",
                               _tr(_rotate(pool, user.telegram_id, now.date().toordinal()), user).format(count=fresh),
                               "search:start", True)
                await s.execute(update(Ban).where(Ban.user_id.in_(ids), Ban.is_active.is_(True),
                    Ban.expires_at <= now).values(is_active=False))
                if notices:
                    if s.bind.dialect.name == "postgresql":
                        from sqlalchemy.dialects.postgresql import insert
                    else:
                        from sqlalchemy.dialects.sqlite import insert
                    await s.execute(insert(Notification).values(notices).on_conflict_do_nothing(index_elements=["dedupe_key"]))
        await self.owner_digest(now)
        async with async_session.begin() as s:
            if s.bind.dialect.name == "postgresql":
                await s.execute(text("SELECT pg_advisory_xact_lock(8873771497)"))
            offers = (await s.scalars(select(Offer).where(Offer.status == "reserved").with_for_update())).all()
            for offer in offers:
                payment = await s.get(Payment, offer.reserved_payment_id)
                if payment and payment.status == "created" and payment.expires_at <= now:
                    payment.status = "failed"
                    offer.status = "active" if offer.ends_at > now else "expired"
                    offer.reserved_payment_id = None

    async def owner_digest(self, now):
        """Report the signups the per-user notice deliberately skipped.

        Covers the last completed hour and counts only what was not already
        sent one by one, so a quiet hour produces nothing and a spike produces
        exactly one line instead of hundreds of messages.
        """
        import config
        if not config.OWNER_ID:
            return
        hour_end = now.replace(minute=0, second=0, microsecond=0)
        hour_start = hour_end - timedelta(hours=1)
        async with async_session.begin() as s:
            joined = await s.scalar(select(func.count(User.id)).where(
                User.created_at >= hour_start, User.created_at < hour_end)) or 0
            if not joined:
                return
            reported = await s.scalar(select(func.count(Notification.id)).where(
                Notification.type == "owner_new_user",
                Notification.scheduled_at >= hour_start, Notification.scheduled_at < hour_end)) or 0
            missing = joined - reported
            if missing <= 0:
                return
            day_start = hour_end.replace(hour=0)
            today = await s.scalar(select(func.count(User.id)).where(User.created_at >= day_start)) or 0
            reached_menu = await s.scalar(select(func.count(User.id)).where(
                User.created_at >= day_start, User.onboarding_completed.is_(True))) or 0
            await enqueue(s, config.OWNER_ID, "owner_digest",
                          f"owner_digest:{config.OWNER_ID}:{hour_start.isoformat()}",
                          f"Сводка за {hour_start:%H:%M}–{hour_end:%H:%M} UTC\n"
                          f"Новых пользователей: {joined} (поштучно показано {reported})\n\n"
                          f"За сегодня: {today} новых, из них дошли до меню {reached_menu}.")

    async def next(self):
        while True:
            async with async_session.begin() as s:
                now = utcnow()
                n = await s.scalar(select(Notification).where(Notification.status.in_(["queued","sending"]), Notification.scheduled_at<=now)
                    .order_by(Notification.marketing, Notification.id).with_for_update(skip_locked=True).limit(1))
                if not n:
                    return None
                if n.type == "onboarding_nudge" and await s.scalar(select(User.onboarding_completed).where(
                        User.telegram_id == n.user_id)):
                    n.status = "skipped"
                    continue
                if n.type == "reveal_request":
                    from models.chat import RevealRequest
                    request = await s.get(RevealRequest, n.callback_data)
                    current = await s.scalar(select(Chat.id).where(Chat.session_id == n.callback_data, Chat.status == "active"))
                    if not current or not request or request.status != "pending":
                        n.status = "skipped"
                        continue
                if n.type=="match" and not await s.scalar(select(Chat.id).where(Chat.session_id==n.callback_data,Chat.status=="active")):
                    n.status="skipped"
                    continue
                if n.type in {"chat_ended", "reconnect_prompt"} and await s.get(ActiveParticipant, n.user_id):
                    n.status = "skipped"
                    continue
                user = await s.scalar(select(User).where(User.telegram_id==n.user_id))
                marketing_disabled = n.marketing and not json.loads(user.notification_settings or "{}").get("marketing", True) if user else True
                if n.marketing:
                    from models.operations import BotSetting
                    flag=await s.get(BotSetting,"marketing_enabled")
                    marketing_disabled=marketing_disabled or bool(flag and not flag.value_json.get("value",1))
                owner_types = {"owner_new_user", "startup", "backup_failed", "owner_report", "owner_first_payment"}
                owner_notice = n.type in owner_types and n.user_id == config.OWNER_ID
                if (n.type in owner_types and not owner_notice) or (not owner_notice and (
                        not user or (banned(user) and n.type != "ban") or user.deleted_at
                        or user.bot_blocked_at or marketing_disabled)):
                    n.status = "skipped"
                    return None
                cooldown = await s.scalar(select(func.max(Notification.scheduled_at)).where(
                    Notification.user_id == n.user_id, Notification.id != n.id,
                    Notification.status.in_(["queued", "sending"]),
                    Notification.last_error == "rate_limit"))
                if owner_notice:
                    sent = await s.scalar(select(func.max(Notification.sent_at)).where(
                        Notification.user_id == n.user_id, Notification.type == "owner_new_user",
                        Notification.status == "sent"))
                    if sent:
                        cooldown = max(cooldown or sent, sent+timedelta(seconds=3))
                if cooldown and cooldown > now:
                    n.scheduled_at = cooldown
                    return None
                if n.attempt_count >= 5:
                    n.status = "failed"
                    if n.broadcast_id:
                        job=await s.get(Broadcast,n.broadcast_id,with_for_update=True)
                        job.failed+=1
                    return None
                if n.marketing:
                    recent=await s.scalar(select(Notification.id).where(Notification.user_id==n.user_id,Notification.id!=n.id,Notification.marketing.is_(True),Notification.status=="sent",Notification.sent_at>now-timedelta(days=1)).limit(1))
                    if recent:
                        n.scheduled_at=now+timedelta(days=1)
                        return None
                n.status, n.scheduled_at = "sending", now+timedelta(minutes=2)
                n.attempt_count += 1
                return n

    async def finish(self, notification_id, status, error=None, retry_after=None):
        async with async_session.begin() as s:
            n = await s.get(Notification, notification_id, with_for_update=True)
            if not n or n.status in {"sent","failed","skipped"}:
                return
            if status not in {"sent","failed","queued"}:
                raise ValueError("Invalid notification status")
            n.status, n.last_error = status, error
            if status=="sent":
                n.sent_at=utcnow()
            if retry_after is not None:
                n.status="queued"
                n.scheduled_at=utcnow()+timedelta(seconds=retry_after)
            # Share the recipient cooldown with queued messages, not only the
            # rejected item; otherwise every pending notice hits the same limit.
            delay = retry_after if error == "rate_limit" else (
                3 if status == "sent" and n.type == "owner_new_user" else None)
            if delay is not None:
                deadline = utcnow()+timedelta(seconds=delay)
                await s.execute(update(Notification).where(
                    Notification.user_id == n.user_id, Notification.status == "queued",
                    Notification.scheduled_at < deadline).values(scheduled_at=deadline))
            if error=="forbidden":
                user=await s.scalar(select(User).where(User.telegram_id==n.user_id))
                if user:
                    user.bot_blocked_at=utcnow()
            if n.broadcast_id and status in {"sent","failed"} and retry_after is None:
                job=await s.get(Broadcast,n.broadcast_id,with_for_update=True)
                if status=="sent":
                    job.sent+=1
                else:
                    job.failed+=1
                    job.blocked+=int(error=="forbidden")

    async def expand_broadcast(self):
        async with async_session.begin() as s:
            pending_jobs=(await s.scalars(select(Broadcast).where(Broadcast.status=="sending").with_for_update(skip_locked=True))).all()
            for item in pending_jobs:
                pending=await s.scalar(select(Notification.id).where(Notification.broadcast_id==item.id,Notification.status.in_(["queued","sending"])).limit(1))
                if not pending:
                    item.status,item.finished_at="completed",utcnow()
            job=await s.scalar(select(Broadcast).where(Broadcast.status=="queued").with_for_update(skip_locked=True).limit(1))
            if not job:
                return
            q=broadcast_segment(select(User).where(User.telegram_id>job.cursor), job.segment).order_by(User.telegram_id).limit(100)
            users=(await s.scalars(q)).all()
            for user in users:
                await enqueue(s,user.telegram_id,"broadcast",f"broadcast:{job.id}:{user.telegram_id}",job.content,marketing=True,broadcast_id=job.id)
                job.cursor=user.telegram_id
            if len(users)<100:
                job.status="sending"

    async def broadcast_counts(self, s, job_id):
        rows = (await s.execute(select(Notification.status, func.count(Notification.id))
                                .where(Notification.broadcast_id == job_id)
                                .group_by(Notification.status))).all()
        return {status: count for status, count in rows}

    async def progress_updates(self):
        """Return live progress for broadcasts that have an admin message to edit.

        Transport belongs to the worker: read counters and stamp the refresh time.
        The worker stops tracking only after the final render is acknowledged.
        """
        updates = []
        async with async_session.begin() as s:
            now = utcnow()
            jobs = (await s.scalars(select(Broadcast).where(
                Broadcast.progress_message_id.is_not(None),
                or_(Broadcast.progress_at.is_(None), Broadcast.progress_at <= now-timedelta(seconds=5)))
                .order_by(Broadcast.id).with_for_update(skip_locked=True).limit(5))).all()
            for job in jobs:
                counts = await self.broadcast_counts(s, job.id)
                poll_totals = None
                if job.kind == "poll":
                    poll_rows = (await s.scalars(select(PollVote.counts).where(
                        PollVote.broadcast_id == job.id))).all()
                    for raw in poll_rows:
                        parsed = json.loads(raw)
                        poll_totals = parsed if poll_totals is None else [a+b for a, b in zip(poll_totals, parsed)]
                updates.append({"chat_id": job.progress_chat_id, "message_id": job.progress_message_id,
                                "job": job, "counts": counts, "poll_totals": poll_totals,
                                "final": job.status == "completed"})
                job.progress_at = now
        return updates

    async def defer_progress(self, job_id, retry_after):
        async with async_session.begin() as s:
            job = await s.get(Broadcast, job_id, with_for_update=True)
            if job:
                job.progress_at = utcnow()+timedelta(seconds=max(0, retry_after)-5)

    async def drop_progress(self, job_id):
        """Stop tracking a message the admin deleted or that Telegram rejected."""
        async with async_session.begin() as s:
            job = await s.get(Broadcast, job_id, with_for_update=True)
            if job:
                job.progress_message_id = None
