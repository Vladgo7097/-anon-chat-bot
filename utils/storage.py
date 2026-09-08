"""Permit stateless help during a Redis outage; never pretend a write succeeded."""
import logging
from aiogram.fsm.storage.redis import RedisStorage
from redis.exceptions import RedisError


class AvailableRedisStorage(RedisStorage):
    async def get_state(self, key):
        try:
            return await super().get_state(key)
        except RedisError:
            logging.getLogger(__name__).warning("fsm_read_unavailable", extra={"error_code": "RedisError"})
            return None
