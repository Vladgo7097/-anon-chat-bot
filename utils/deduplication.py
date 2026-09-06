import asyncio
import logging
from weakref import WeakValueDictionary
from aiogram import BaseMiddleware
from redis.exceptions import RedisError
from services.chat_manager import get_redis


class UpdateDeduplication(BaseMiddleware):
    """Deduplicate completed updates across restarts of the single polling bot."""
    def __init__(self):
        self.locks = WeakValueDictionary()

    async def __call__(self, handler, event, data):
        key = f"update:done:{data['bot'].id}:{event.update_id}"
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        async with lock:
            r = None
            try:
                r = await get_redis()
                if await r.exists(key):
                    return
            except RedisError:
                logging.getLogger(__name__).warning("update_dedupe_unavailable")
            # Exceptions must propagate; failed updates must remain retryable.
            result = await handler(event, data)
            if r is not None:
                try:
                    await r.set(key, "1", ex=86400)
                except RedisError:
                    logging.getLogger(__name__).warning("update_dedupe_write_failed")
            return result
