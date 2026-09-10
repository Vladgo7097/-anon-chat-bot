"""Every Redis client the app builds must time out instead of hanging.

None of them set socket_timeout/socket_connect_timeout, so a stalled TCP
connection -- a network blip, a stuck connection reused from the pool, Redis
itself being briefly slow -- blocked the calling coroutine forever. The
services already anticipate Redis being unavailable (redis_fallback,
AvailableRedisStorage.get_state both catch RedisError), but none of that
helps if the call never raises in the first place. MatchingService.enqueue()
is the first Redis call in "start a search" and has no fallback at all, so
this is what made pressing "Найти собеседника" hang with no error and no
way out -- the per-user asyncio.Lock in handlers/search.py stayed held, so
even retrying hung again.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MainRedisPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from services import chat_manager
        await chat_manager.close_redis()

    async def test_the_shared_pool_cannot_block_forever(self):
        from services.chat_manager import get_redis
        r = await get_redis()
        kwargs = r.connection_pool.connection_kwargs
        self.assertIsNotNone(kwargs.get("socket_connect_timeout"),
                             "a dead Redis host would hang the connection attempt forever")
        self.assertIsNotNone(kwargs.get("socket_timeout"),
                             "a stalled connection would hang every command forever")

    async def test_stale_pooled_connections_are_detected_proactively(self):
        from services.chat_manager import get_redis
        r = await get_redis()
        # A connection reused from the pool without a health check can be a
        # half-dead TCP socket that only fails (or hangs) on the next command.
        self.assertGreater(r.connection_pool.connection_kwargs.get("health_check_interval", 0), 0)


class FsmStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_py_passes_timeouts_to_the_fsm_storage(self):
        # Reads bot.py's own source rather than hardcoding the call here, so
        # this fails if that call is ever edited back to a bare from_url().
        import re
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
        call = re.search(r"AvailableRedisStorage\.from_url\((.*?)\)\)", source, re.S).group(1)
        self.assertIn("connection_kwargs", call)
        self.assertIn("socket_connect_timeout", call)
        self.assertIn("socket_timeout", call)

    async def test_the_fsm_redis_client_actually_gets_them(self):
        from utils.storage import AvailableRedisStorage
        storage = AvailableRedisStorage.from_url(
            "redis://localhost:6379/0",
            connection_kwargs={"socket_connect_timeout": 3, "socket_timeout": 5, "health_check_interval": 30})
        try:
            kwargs = storage.redis.connection_pool.connection_kwargs
            self.assertIsNotNone(kwargs.get("socket_connect_timeout"))
            self.assertIsNotNone(kwargs.get("socket_timeout"))
        finally:
            await storage.redis.aclose()


class WebLandingRedisTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_landing_page_counters_also_time_out(self):
        import web.app as web_app
        web_app._redis = None
        try:
            r = web_app._get_redis()
            kwargs = r.connection_pool.connection_kwargs
            self.assertIsNotNone(kwargs.get("socket_connect_timeout"))
            self.assertIsNotNone(kwargs.get("socket_timeout"))
        finally:
            if web_app._redis is not None:
                await web_app._redis.aclose()
            web_app._redis = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
