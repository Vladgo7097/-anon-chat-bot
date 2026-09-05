"""Redis cache and compatibility entry points; durable sessions live in PostgreSQL."""
import time
import redis.asyncio as aioredis
import config

_pool = None


async def get_redis():
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(config.REDIS_URL, decode_responses=True, max_connections=30)
    return _pool


async def close_redis():
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def set_online(user_id):
    r = await get_redis()
    now = time.time()
    async with r.pipeline(transaction=True) as p:
        p.zadd("online:heartbeats", {str(user_id): now})
        p.zremrangebyscore("online:heartbeats", "-inf", now-config.ONLINE_UPDATE_INTERVAL*3)
        await p.execute()


async def get_online_count():
    r = await get_redis()
    return await r.zcount("online:heartbeats", time.time()-config.ONLINE_UPDATE_INTERVAL*3, "+inf")


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
