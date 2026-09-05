"""
Redis-based chat state management.

Keys:
  search_queue              – SortedSet: user_id -> timestamp (searching users)
  active_chat:{user_id}    -> partner_id (string)
  chat_started:{user_id}   -> timestamp
  online_users              – Set of user_ids seen recently
  last_message:{user_id}   -> timestamp (anti-flood)
  search_gender:{user_id}  -> m/f/a
  reveal:{min}:{max}       -> Set of user_ids who requested reveal
  instant_search:{user_id}  -> "1" if paid for instant search
"""
import time
import asyncio
from typing import Optional

import redis.asyncio as aioredis

import config

_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _pool


async def close_redis():
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None


# ── Online tracking ──────────────────────────────────────────────

async def set_online(user_id: int):
    r = await get_redis()
    await r.sadd("online_users", str(user_id))
    await r.expire("online_users", config.ONLINE_UPDATE_INTERVAL * 3)


async def get_online_count() -> int:
    r = await get_redis()
    return await r.scard("online_users")


# ── Anti-flood ───────────────────────────────────────────────────

async def check_flood(user_id: int) -> bool:
    """Returns True if message is flood (too fast)."""
    r = await get_redis()
    key = f"last_message:{user_id}"
    last = await r.get(key)
    now = time.time()
    if last and (now - float(last)) < config.ANTI_FLOOD_DELAY:
        return True
    await r.set(key, str(now), ex=2)
    return False


# ── Search queue ─────────────────────────────────────────────────

async def join_search(user_id: int, gender_filter: str = "a"):
    r = await get_redis()
    await r.zadd("search_queue", {str(user_id): time.time()})
    await r.set(f"search_gender:{user_id}", gender_filter, ex=3600)


async def leave_search(user_id: int):
    r = await get_redis()
    await r.zrem("search_queue", str(user_id))
    await r.delete(f"search_gender:{user_id}")


async def get_search_queue_length() -> int:
    r = await get_redis()
    return await r.zcard("search_queue")


async def pop_from_search(my_id: int, my_gender: Optional[str] = None) -> Optional[int]:
    """Try to find a partner from the search queue with optional gender filter."""
    r = await get_redis()

    candidates = await r.zrangebyscore(
        "search_queue", "-inf", "+inf", withscores=True
    )

    for user_id_str, score in candidates:
        uid = int(user_id_str)
        if uid == my_id:
            continue

        if await r.get(f"active_chat:{uid}"):
            await r.zrem("search_queue", user_id_str)
            continue

        partner_gender_pref = await r.get(f"search_gender:{uid}")
        if partner_gender_pref and partner_gender_pref != "a" and my_gender:
            if partner_gender_pref != my_gender:
                continue

        await r.zrem("search_queue", user_id_str)
        return uid

    return None


async def search_with_timeout(my_id: int, my_gender: Optional[str] = None, timeout: float = 1.0) -> Optional[int]:
    """Search with brief polling timeout."""
    result = await pop_from_search(my_id, my_gender)
    if result:
        return result
    await asyncio.sleep(timeout)
    return await pop_from_search(my_id, my_gender)


# ── Active chat ──────────────────────────────────────────────────

async def start_chat(user1_id: int, user2_id: int):
    r = await get_redis()
    now = str(time.time())
    await r.set(f"active_chat:{user1_id}", str(user2_id), ex=config.INACTIVE_CHAT_TTL)
    await r.set(f"active_chat:{user2_id}", str(user1_id), ex=config.INACTIVE_CHAT_TTL)
    await r.set(f"chat_started:{user1_id}", now, ex=86400)
    await r.set(f"chat_started:{user2_id}", now, ex=86400)
    await r.zrem("search_queue", str(user1_id), str(user2_id))


async def get_partner(user_id: int) -> Optional[int]:
    r = await get_redis()
    val = await r.get(f"active_chat:{user_id}")
    return int(val) if val else None


async def end_chat(user_id: int) -> Optional[int]:
    r = await get_redis()
    partner_id = await get_partner(user_id)
    if not partner_id:
        return None

    # Both participants must share the same lock when ending their dialogue.
    lock_key = f"end_chat:{min(user_id, partner_id)}:{max(user_id, partner_id)}"
    async with r.lock(lock_key, timeout=60, blocking_timeout=10):
        if await get_partner(user_id) != partner_id:
            return None
        started = await r.get(f"chat_started:{user_id}")
        duration = max(0, int(time.time() - float(started))) if started else 0
        from utils.db import save_chat
        await save_chat(user_id, partner_id, duration)
        await r.delete(
            f"active_chat:{user_id}", f"active_chat:{partner_id}",
            f"chat_started:{user_id}", f"chat_started:{partner_id}",
            self_chat_key(user_id, partner_id),
        )
    return partner_id


async def refresh_chat_ttl(user_id: int):
    r = await get_redis()
    partner_id = await get_partner(user_id)
    if partner_id:
        await r.expire(f"active_chat:{user_id}", config.INACTIVE_CHAT_TTL)
        await r.expire(f"active_chat:{partner_id}", config.INACTIVE_CHAT_TTL)


def self_chat_key(u1: int, u2: int) -> str:
    return f"reveal:{min(u1, u2)}:{max(u1, u2)}"


# ── Reveal requests ──────────────────────────────────────────────

async def request_reveal(user_id: int, partner_id: int) -> bool:
    """Returns True if mutual (both requested)."""
    r = await get_redis()
    key = self_chat_key(user_id, partner_id)
    members = await r.smembers(key)
    await r.sadd(key, str(user_id))
    await r.expire(key, 86400)
    if str(partner_id) in members:
        return True
    return False


async def check_reveal(user_id: int, partner_id: int) -> bool:
    r = await get_redis()
    key = self_chat_key(user_id, partner_id)
    members = await r.smembers(key)
    return str(partner_id) in members


# ── Instant search payment ───────────────────────────────────────

async def set_instant_search(user_id: int):
    r = await get_redis()
    await r.set(f"instant_search:{user_id}", "1", ex=300)
