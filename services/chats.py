from services.result import ServiceResult
from repositories.chats import ChatRepository


class ChatService:
    def __init__(self):
        self.repo = ChatRepository()

    async def active(self, user_id):
        return await self.repo.active(user_id)

    async def sync(self, chat):
        from services.chat_manager import get_redis
        from repositories.chats import lock_matching
        from models.base import async_session
        from models.chat import Chat
        from sqlalchemy import select
        r = await get_redis()
        async with async_session.begin() as s:
            await lock_matching(s)
            current=await s.scalar(select(Chat).where(Chat.session_id==chat.session_id,Chat.status=="active"))
            if not current:
                return
            async with r.pipeline(transaction=True) as pipe:
                for uid, partner in ((chat.user1_id, chat.user2_id), (chat.user2_id, chat.user1_id)):
                    pipe.set(f"active_chat:{uid}", str(partner))
                    pipe.set(f"active_session:{uid}", chat.session_id)
                pipe.zrem("search_queue", str(chat.user1_id), str(chat.user2_id))
                await pipe.execute()

    async def end_chat(self, user_id, session_id=None, reason="stop"):
        chat = await self.repo.end(user_id, session_id, reason)
        if not chat:
            return ServiceResult(False, "ALREADY_ENDED")
        from services.chat_manager import get_redis
        r = await get_redis()
        for uid in (chat.user1_id, chat.user2_id):
            await r.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1],KEYS[2],KEYS[3]) end return 0",
                         3, f"active_session:{uid}", f"active_chat:{uid}", f"chat_started:{uid}", chat.session_id)
        return ServiceResult(True, "CHAT_ENDED", {"chat": chat})

    async def reconcile(self):
        from services.chat_manager import get_redis
        import config
        from utils.time import utcnow
        r = await get_redis()
        sessions = await self.repo.all_active()
        from models.base import async_session
        from models.chat import ActiveParticipant
        from repositories.chats import lock_matching
        async for key in r.scan_iter(match="active_chat:*"):
            uid = key.split(":")[-1]
            async with async_session.begin() as s:
                await lock_matching(s)
                if not await s.get(ActiveParticipant, int(uid)):
                    await r.delete(key, f"active_session:{uid}", f"chat_started:{uid}")
        ended = []
        from services.settings import bot_value
        timeout = await bot_value("inactive_chat_ttl", config.INACTIVE_CHAT_TTL)
        for chat in sessions:
            if (utcnow() - (chat.last_activity_at or chat.started_at)).total_seconds() > timeout:
                result = await self.end_chat(chat.user1_id, chat.session_id, "timeout")
                if result.ok:
                    ended.append(chat)
            else:
                await self.sync(chat)
        return ended
