import uuid
from datetime import timedelta
from sqlalchemy import select, update, delete, or_, and_, text
from models.base import async_session
from models.user import User
from models.chat import Chat, ActiveParticipant, Block, Rating, RevealRequest
from services.onboarding import step_for, banned
from utils.time import utcnow


def premium_active(user):
    return bool(user.premium_expires and user.premium_expires > utcnow())


def compatible(a, b, gender_access=()):
    for own, other in ((a, b), (b, a)):
        if step_for(own) != "MENU" or banned(own) or own.deleted_at or own.bot_blocked_at:
            return False
        if premium_active(own) or own.telegram_id in gender_access:
            gender = {"m": "male", "f": "female", "a": "any"}.get(own.settings_gender_filter, own.settings_gender_filter)
            if gender != "any" and gender != other.gender:
                return False
            if premium_active(own) and not own.settings_age_min <= other.age <= own.settings_age_max:
                return False
    return a.telegram_id != b.telegram_id


async def lock_matching(session):
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(8873771496)"))


class ChatRepository:
    async def active(self, user_id):
        async with async_session() as s:
            return await s.scalar(select(Chat).join(ActiveParticipant, ActiveParticipant.session_id == Chat.session_id)
                                  .where(ActiveParticipant.user_id == user_id, Chat.status == "active"))

    async def get(self, session_id, user_id):
        async with async_session() as s:
            return await s.scalar(select(Chat).where(Chat.session_id == session_id,
                or_(Chat.user1_id == user_id, Chat.user2_id == user_id)))

    async def match(self, a_id, b_id, redis):
        async with async_session.begin() as s:
            await lock_matching(s)
            if await redis.zscore("search_queue", str(a_id)) is None or await redis.zscore("search_queue", str(b_id)) is None:
                return None
            if not await redis.exists(f"search:user:{a_id}") or not await redis.exists(f"search:user:{b_id}"):
                return None
            if await s.scalar(select(ActiveParticipant).where(ActiveParticipant.user_id.in_([a_id, b_id])).limit(1)):
                return None
            users = list((await s.scalars(select(User).where(User.telegram_id.in_([a_id, b_id])))).all())
            from services.payments import available_entitlement
            filters = {}
            for uid in (a_id, b_id):
                entitlement = await available_entitlement(s, uid, ["gender_filter_one_match", "gender_filter_1m"])
                if entitlement:
                    filters[uid] = entitlement
            if len(users) != 2 or not compatible(*users, gender_access=filters):
                return None
            blocked = await s.scalar(select(Block).where(or_(
                and_(Block.blocker_id == a_id, Block.blocked_id == b_id),
                and_(Block.blocker_id == b_id, Block.blocked_id == a_id))))
            if blocked:
                return None
            chat = Chat(session_id=uuid.uuid4().hex, user1_id=a_id, user2_id=b_id,
                        status="active", started_at=utcnow(), last_activity_at=utcnow())
            s.add(chat)
            await s.flush()
            s.add_all([ActiveParticipant(user_id=uid, session_id=chat.session_id) for uid in (a_id, b_id)])
            from services.growth import event
            for uid in (a_id, b_id):
                await event(s, uid, "chat_started", f"chat_started:{chat.session_id}:{uid}")
                from services.notifications import enqueue
                await enqueue(s,uid,"match",f"match:{chat.session_id}:{uid}","<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Собеседник найден!\n\nПишите первым! <tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji>",chat.session_id)
            for user in users:
                entitlement = filters.get(user.telegram_id)
                if entitlement and entitlement.kind == "gender_filter_one_match" and not premium_active(user) and user.settings_gender_filter != "a":
                    entitlement.remaining_uses -= 1
                priority = await available_entitlement(s, user.telegram_id, ["instant_search"])
                if priority and not premium_active(user):
                    priority.remaining_uses -= 1
        return chat

    async def end(self, user_id, session_id=None, reason="stop"):
        async with async_session.begin() as s:
            await lock_matching(s)
            query = select(Chat).where(Chat.status == "active", or_(Chat.user1_id == user_id, Chat.user2_id == user_id))
            if session_id:
                query = query.where(Chat.session_id == session_id)
            chat = await s.scalar(query.with_for_update())
            if not chat:
                return None
            now = utcnow()
            chat.status, chat.ended_at, chat.end_reason, chat.ended_by = "ended", now, reason, user_id
            chat.duration_seconds = max(0, int((now - chat.started_at).total_seconds()))
            await s.execute(delete(ActiveParticipant).where(ActiveParticipant.session_id == chat.session_id))
            await s.execute(update(User).where(User.telegram_id.in_([chat.user1_id, chat.user2_id])).values(
                chats_count=User.chats_count + 1,
                total_chat_seconds=User.total_chat_seconds + chat.duration_seconds))
            await s.execute(update(RevealRequest).where(RevealRequest.chat_session_id == chat.session_id,
                RevealRequest.status == "pending").values(status="expired", resolved_at=now))
            from services.growth import completed_chat, event
            users = (await s.scalars(select(User).where(User.telegram_id.in_([chat.user1_id,chat.user2_id])).order_by(User.telegram_id).with_for_update())).all()
            for user in users:
                await completed_chat(s, user)
                await event(s, user.telegram_id, "chat_ended", f"chat_ended:{chat.session_id}:{user.telegram_id}")
                if reason in {"ban","account_deleted","unreachable","timeout"}:
                    from services.notifications import enqueue
                    await enqueue(s,user.telegram_id,"chat_ended",f"ended:{chat.session_id}:{user.telegram_id}","Диалог завершён по неактивности." if reason=="timeout" else "Диалог завершён. Можно начать новый поиск.","menu")
        return chat

    async def touch(self, session_id):
        async with async_session.begin() as s:
            await s.execute(update(Chat).where(Chat.session_id == session_id, Chat.status == "active").values(last_activity_at=utcnow()))

    async def all_active(self):
        async with async_session() as s:
            return list((await s.scalars(select(Chat).where(Chat.status == "active"))).all())

    async def rate(self, session_id, user_id, value):
        if value not in {-1, 1}:
            return False
        async with async_session.begin() as s:
            chat = await s.scalar(select(Chat).where(Chat.session_id == session_id, Chat.status == "ended",
                or_(Chat.user1_id == user_id, Chat.user2_id == user_id)).with_for_update())
            if not chat or await s.scalar(select(Rating).where(Rating.chat_session_id == session_id, Rating.rater_id == user_id)):
                return False
            partner = chat.user2_id if chat.user1_id == user_id else chat.user1_id
            s.add(Rating(chat_session_id=session_id, rater_id=user_id, rated_id=partner, value=value))
            user = await s.scalar(select(User).where(User.telegram_id == partner).with_for_update())
            if value == 1:
                user.likes_received += 1
            else:
                user.dislikes_received += 1
            user.positive_rating_pct = round(100 * user.likes_received / (user.likes_received + user.dislikes_received), 1)
            from services.notifications import enqueue
            await enqueue(s, partner, "rating", f"rating:{session_id}:{user_id}", "<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Тебя оценили после последнего диалога!" if value==1 else "<tg-emoji emoji-id=\"5348132683304156113\">👎</tg-emoji> Получена оценка после диалога.", "profile")
        return True
