import re

from services.chats import ChatService
from services.result import ServiceResult
from repositories.chats import ChatRepository


class RelayService:
    SUPPORTED={"text","photo","voice","sticker","video","video_note","animation","audio"}
    CONTACT_PATTERN = re.compile(
        r"(?ix)(?:https?://)?t\.me/[a-z0-9_+/-]+|(?<!\w)@[a-z][a-z0-9_]{4,31}|"
        r"(?<!\d)(?:\+?\d[\s().-]*){7,15}(?!\d)"
    )

    @classmethod
    def contains_contact(cls, text):
        return bool(text and cls.CONTACT_PATTERN.search(text))

    async def contact_warning_required(self, user_id, session_id, text):
        if not self.contains_contact(text):
            return False
        from services.chat_manager import get_redis
        from redis.exceptions import RedisError
        try:
            return not await (await get_redis()).exists(f"contact:allowed:{session_id}:{user_id}")
        except RedisError:
            return True

    async def allow_contacts(self, user_id, session_id):
        from services.chat_manager import get_redis
        await (await get_redis()).set(f"contact:allowed:{session_id}:{user_id}", "1", ex=86400)

    @staticmethod
    def file_id(message):
        """The file_id of the media a message carries, if any.

        Stored only in the report snapshot -- the relay itself uses copy_message
        and never needs to re-upload a file.
        """
        kind = getattr(message, "content_type", None)
        if kind == "photo":
            photos = getattr(message, "photo", None) or []
            return photos[-1].file_id if photos else None
        value = getattr(message, kind, None) if kind else None
        return getattr(value, "file_id", None)

    async def remember_context(self, session_id, user_id, text=None, kind="text", file_id=None):
        """Keep the last eight messages for a possible report.

        JSON instead of "id:text" so a report can snapshot a photo or a voice
        message too. Old plain rows are still parsed by report_context().
        """
        if not text and not file_id:
            return
        import json
        from services.chat_manager import get_redis
        from redis.exceptions import RedisError
        try:
            key = f"report:context:{session_id}"
            value = json.dumps({"u": user_id, "k": kind or "text", "f": file_id,
                                "t": (text or "")[:300]}, ensure_ascii=False)
            r = await get_redis()
            async with r.pipeline(transaction=True) as pipe:
                pipe.rpush(key, value)
                pipe.ltrim(key, -8, -1)
                pipe.expire(key, 1800)
                await pipe.execute()
        except RedisError:
            return

    async def report_context(self, session_id, clear=False):
        """Structured buffer items: user_id, kind, file_id, text.

        A plain "id:text" row is still accepted: it was written by an older
        process during a rolling deploy and must not vanish from a report.
        """
        import json
        from services.chat_manager import get_redis
        from redis.exceptions import RedisError
        try:
            key = f"report:context:{session_id}"
            r = await get_redis()
            values = await r.lrange(key, 0, -1)
            if clear:
                await r.delete(key)
        except RedisError:
            return []
        items = []
        for value in values:
            if isinstance(value, bytes):
                value = value.decode()
            try:
                data = json.loads(value)
                if isinstance(data, dict) and "u" in data:
                    items.append({
                        "user_id": int(data["u"]),
                        "kind": data.get("k") or "text",
                        "file_id": data.get("f"),
                        "text": data.get("t") or "",
                    })
                    continue
            except (TypeError, ValueError):
                pass
            sender, separator, text = value.partition(":")
            if separator and sender.isdigit():
                items.append({"user_id": int(sender), "kind": "text",
                              "file_id": None, "text": text})
        return items

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
                pipe.lpush(order, *sorted(fields.keys()))
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
