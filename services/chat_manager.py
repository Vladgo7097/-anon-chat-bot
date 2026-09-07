"""Redis cache and compatibility entry points; durable sessions live in PostgreSQL."""
import asyncio
import time
import redis.asyncio as aioredis
import config
import logging
from functools import wraps
from redis.exceptions import RedisError


def redis_fallback(default):
    def decorate(fn):
        @wraps(fn)
        async def call(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except RedisError:
                logging.getLogger(__name__).warning("redis_unavailable", extra={"error_code": "RedisError"})
                return default
        return call
    return decorate

_pool = None
_pool_loop = None


async def get_redis():
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is None or _pool_loop is not loop:
        _pool = aioredis.from_url(config.REDIS_URL, decode_responses=True, max_connections=30)
        _pool_loop = loop
    return _pool


async def close_redis():
    global _pool, _pool_loop
    if _pool is not None:
        if _pool_loop is asyncio.get_running_loop():
            await _pool.aclose()
        _pool = None
        _pool_loop = None


@redis_fallback(None)
async def set_online(user_id):
    r = await get_redis()
    now = time.time()
    async with r.pipeline(transaction=True) as p:
        p.zadd("online:heartbeats", {str(user_id): now})
        p.zremrangebyscore("online:heartbeats", "-inf", now-config.ONLINE_UPDATE_INTERVAL*3)
        await p.execute()


@redis_fallback("—")
async def get_online_count():
    r = await get_redis()
    return await r.zcount("online:heartbeats", time.time()-config.ONLINE_UPDATE_INTERVAL*3, "+inf")


@redis_fallback(False)
async def check_flood(user_id):
    r = await get_redis()
    return not await r.set(f"rate:message:{user_id}", "1", nx=True, px=int(config.ANTI_FLOOD_DELAY*1000))


async def get_partner(user_id):
    from services.chats import ChatService
    chat = await ChatService().active(user_id)
    return (chat.user2_id if chat.user1_id == user_id else chat.user1_id) if chat else None


async def end_chat(user_id):
    from services.chats import ChatService
    result = await ChatService().end_chat(user_id)
    if result.ok:
        c = result.data["chat"]
        return c.user2_id if c.user1_id == user_id else c.user1_id


async def leave_search(user_id):
    from services.matching import MatchingService
    await MatchingService().cancel(user_id)


async def get_search_queue_length():
    return await (await get_redis()).zcard("search_queue")


async def set_instant_search(user_id):
    r = await get_redis()
    await r.zadd("search_queue", {str(user_id): time.time()-86400}, xx=True)
