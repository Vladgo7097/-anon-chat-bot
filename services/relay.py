from services.chats import ChatService
from services.result import ServiceResult
from repositories.chats import ChatRepository


class RelayService:
    SUPPORTED={"text","photo","voice","sticker","video","video_note","animation"}

    async def prepare(self,user_id,content_type):
        chat=await ChatService().active(user_id)
        if not chat:
            return ServiceResult(False,"NOT_CHATTING")
        if content_type not in self.SUPPORTED:
            return ServiceResult(False,"UNSUPPORTED_CONTENT")
        partner=chat.user2_id if chat.user1_id==user_id else chat.user1_id
        return ServiceResult(True,"RELAY",{"partner":partner,"session_id":chat.session_id})

    async def delivered(self,user_id,session_id,source_chat,message_id):
        await ChatRepository().touch(session_id)

    async def reply_target(self, session_id, user_id, message_id):
        from services.chat_manager import get_redis
        from redis.exceptions import RedisError
        try:
            value = await (await get_redis()).hget(f"relay:map:{session_id}", f"{user_id}:{message_id}")
            return int(value) if value else None
        except RedisError:
            return None

    async def remember(self, session_id, user_id, message_id, partner, copied_id):
        from services.chat_manager import get_redis
        from redis.exceptions import RedisError
        try:
            r = await get_redis()
            key, order = f"relay:map:{session_id}", f"relay:order:{session_id}"
            fields = {f"{user_id}:{message_id}": str(copied_id), f"{partner}:{copied_id}": str(message_id)}
            async with r.pipeline(transaction=True) as pipe:
                pipe.hset(key, mapping=fields)
                pipe.lpush(order, *fields)
                pipe.expire(key, 86400)
                pipe.expire(order, 86400)
                await pipe.execute()
            old = await r.lrange(order, 2000, -1)
            if old:
                async with r.pipeline(transaction=True) as pipe:
                    pipe.hdel(key, *old)
                    pipe.ltrim(order, 0, 1999)
                    await pipe.execute()
        except RedisError:
            import logging
            logging.getLogger(__name__).warning("reply_map_unavailable", extra={"chat_session_id": session_id})
