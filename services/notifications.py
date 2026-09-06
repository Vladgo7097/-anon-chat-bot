"""Durable outbox. Telegram transport is owned by workers, not services."""
import json
from datetime import timedelta
from sqlalchemy import select, func, or_, update
from models.base import async_session
from models.operations import Notification, Broadcast, Offer
from models.payment import Payment
from models.user import User
from models.chat import Chat, ActiveParticipant
from utils.time import utcnow
from services.onboarding import banned


async def enqueue(s, user_id, kind, key, content, callback=None, marketing=False, broadcast_id=None):
    if not await s.scalar(select(Notification).where(Notification.dedupe_key == key)):
        s.add(Notification(user_id=user_id, type=kind, dedupe_key=key, text=content, callback_data=callback,
                           marketing=marketing, broadcast_id=broadcast_id))


class NotificationService:
    async def schedule(self):
        from services.settings import bot_value
        marketing_enabled=await bot_value("marketing_enabled",1)
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            now = utcnow()
            from models.ban import Ban
            expired=(await s.scalars(select(User).where(User.is_banned.is_(True),User.ban_expires<=now).with_for_update())).all()
            for user in expired:
                await enqueue(s,user.telegram_id,"unban",f"unban_expired:{user.telegram_id}:{user.ban_expires.isoformat()}","<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Срок блокировки истёк. Добро пожаловать обратно!","menu")
                user.is_banned=False
            await s.execute(update(Ban).where(Ban.is_active.is_(True),Ban.expires_at<=now).values(is_active=False))
            await s.execute(update(User).where(User.is_premium.is_(True),User.premium_expires<=now).values(is_premium=False))
            users = (await s.scalars(select(User).where(User.deleted_at.is_(None), User.bot_blocked_at.is_(None),
                User.onboarding_completed.is_(True)).order_by(User.id))).all()
            for user in users:
                if banned(user):
                    continue
                if user.premium_expires and now < user.premium_expires <= now+timedelta(days=1):
                    await enqueue(s, user.telegram_id, "premium_expiry", f"expiry:{user.telegram_id}:{user.premium_expires.isoformat()}", "⏰ Твой Premium заканчивается завтра. Продлить?", "premium")
                marketing = json.loads(user.notification_settings or "{}").get("marketing", True)
                if not marketing or not marketing_enabled:
                    continue
                recent = await s.scalar(select(Notification.id).where(Notification.user_id==user.telegram_id, Notification.marketing.is_(True),
                    Notification.scheduled_at>now-timedelta(hours=24)).limit(1))
                if recent:
                    continue
                if user.last_online and user.last_online < now-timedelta(days=3):
                    count = await s.scalar(select(func.count(Chat.id)).where(Chat.started_at > user.last_online, Chat.status=="ended"))
                    await enqueue(s, user.telegram_id, "inactive", f"inactive:{user.telegram_id}:{now.date()}",
                        f"<tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji> Скучаем! За это время в боте было {count} новых завершённых диалогов.", "search:start", True)
            offers = (await s.scalars(select(Offer).where(Offer.status=="reserved").with_for_update())).all()
            for offer in offers:
                payment = await s.get(Payment, offer.reserved_payment_id)
                if payment and payment.status=="created" and payment.expires_at <= now:
                    payment.status = "failed"
                    offer.status = "active" if offer.ends_at>now else "expired"
                    offer.reserved_payment_id = None

    async def next(self):
        async with async_session.begin() as s:
            now = utcnow()
            n = await s.scalar(select(Notification).where(Notification.status.in_(["queued","sending"]), Notification.scheduled_at<=now)
                .order_by(Notification.id).with_for_update(skip_locked=True).limit(1))
            if not n:
                return None
            if n.type=="match" and not await s.scalar(select(Chat.id).where(Chat.session_id==n.callback_data,Chat.status=="active")):
                n.status="skipped"
                return None
            if n.type == "chat_ended" and await s.get(ActiveParticipant, n.user_id):
                n.status = "skipped"
                return None
            user = await s.scalar(select(User).where(User.telegram_id==n.user_id))
            marketing_disabled = n.marketing and not json.loads(user.notification_settings or "{}").get("marketing", True) if user else True
            if n.marketing:
                from models.operations import BotSetting
                flag=await s.get(BotSetting,"marketing_enabled")
                marketing_disabled=marketing_disabled or bool(flag and not flag.value_json.get("value",1))
            if not user or (banned(user) and n.type != "ban") or user.deleted_at or user.bot_blocked_at or marketing_disabled:
                n.status = "skipped"
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
            if error=="forbidden":
                user=await s.scalar(select(User).where(User.telegram_id==n.user_id))
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
            q=select(User).where(User.telegram_id>job.cursor, User.deleted_at.is_(None), User.bot_blocked_at.is_(None)).order_by(User.telegram_id).limit(100)
            if job.segment=="premium":
                q=q.where(User.premium_expires>utcnow())
            elif job.segment=="inactive":
                q=q.where(User.last_online<utcnow()-timedelta(days=7))
            elif job.segment=="nopay":
                q=q.where(User.total_stars_spent==0)
            users=(await s.scalars(q)).all()
            for user in users:
                await enqueue(s,user.telegram_id,"broadcast",f"broadcast:{job.id}:{user.telegram_id}",job.content,marketing=True,broadcast_id=job.id)
                job.cursor=user.telegram_id
            if len(users)<100:
                job.status="sending"
