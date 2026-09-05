import time
from models.base import async_session
from repositories.chats import ChatRepository, lock_matching, premium_active
from repositories.users import UserRepository
from services.onboarding import step_for, banned
from services.chats import ChatService
from services.result import ServiceResult
from sqlalchemy import select
from models.user import User
from models.chat import ActiveParticipant


class MatchingService:
    async def enqueue(self, user_id):
        from services.chat_manager import get_redis
        from services.settings import bot_value
        import config
        timeout = max(1, int(await bot_value("search_timeout", config.SEARCH_TIMEOUT)))
        r = await get_redis()
        async with async_session.begin() as s:
            await lock_matching(s)
            user = await s.scalar(select(User).where(User.telegram_id==user_id))
            if not user or user.deleted_at or user.bot_blocked_at or step_for(user) != "MENU":
                return ServiceResult(False, "ONBOARDING_REQUIRED")
            if banned(user):
                return ServiceResult(False, "BANNED")
            if await s.get(ActiveParticipant, user_id):
                return ServiceResult(False, "ALREADY_CHATTING")
            from services.payments import available_entitlement
            priority = premium_active(user) or await available_entitlement(s, user_id, ["instant_search"])
            score = time.time() - (86400 if priority else 0)
            if not await r.exists(f"search:user:{user_id}"):
                await r.zrem("search_queue", str(user_id))
            inserted = await r.zadd("search_queue", {str(user_id): score}, nx=True)
            if inserted:
                await r.set(f"search:user:{user_id}", str(time.time()), ex=timeout)
                from services.growth import event
                await event(s,user_id,"search_started",f"search:{user_id}:{time.time_ns()}")
        return ServiceResult(True, "SEARCH_STARTED" if inserted else "ALREADY_SEARCHING")

    async def cancel(self, user_id):
        from services.chat_manager import get_redis
        r = await get_redis()
        async with async_session.begin() as s:
            await lock_matching(s)
            await r.zrem("search_queue", str(user_id))
            await r.delete(f"search:user:{user_id}", f"search_gender:{user_id}")
        return ServiceResult(True, "SEARCH_CANCELLED")

    async def find(self, user_id):
        from services.chat_manager import get_redis
        r = await get_redis()
        if await r.zscore("search_queue", str(user_id)) is None:
            return None
        candidates = await r.zrange("search_queue", 0, 199)
        for value in candidates:
            partner = int(value)
            if partner == user_id:
                continue
            if not await r.exists(f"search:user:{partner}"):
                await r.zrem("search_queue", value)
                continue
            chat = await ChatRepository().match(user_id, partner, r)
            if chat:
                await ChatService().sync(chat)
                return chat
        return None
