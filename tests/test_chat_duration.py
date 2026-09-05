"""Isolated regression checks: no Telegram, Redis or production DB access."""
import ast
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"chat_manager.py": ROOT / "services/chat_manager.py", "rating.py": ROOT / "handlers/rating.py"}


def load_functions(filename, names, namespace):
    tree = ast.parse(SOURCES[filename].read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)


class RedisFake:
    def __init__(self, started="100"):
        self.data = {"active_chat:1": "2", "active_chat:2": "1"}
        if started is not None:
            self.data.update({"chat_started:1": started, "chat_started:2": started})
        self.locks = {}

    async def get(self, key):
        await asyncio.sleep(0)
        return self.data.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    def lock(self, key, **kwargs):
        return self.locks.setdefault(key, asyncio.Lock())


class DurationTests(unittest.IsolatedAsyncioTestCase):
    def setup_manager(self, started="100"):
        redis = RedisFake(started)
        db = types.ModuleType("utils.db")
        db.save_chat = AsyncMock()
        sys.modules["utils.db"] = db
        ns = {"Optional": __import__("typing").Optional,
              "get_redis": AsyncMock(return_value=redis),
              "time": types.SimpleNamespace(time=lambda: 160.9)}
        load_functions("chat_manager.py", {"get_partner", "end_chat", "self_chat_key"}, ns)
        return redis, db, ns

    async def test_actual_duration_saved_without_rating(self):
        redis, db, ns = self.setup_manager()
        self.assertEqual(await ns["end_chat"](1), 2)
        db.save_chat.assert_awaited_once_with(1, 2, 60)
        self.assertEqual(redis.data, {})

    async def test_simultaneous_and_repeated_stop_saved_once(self):
        redis, db, ns = self.setup_manager()
        results = await asyncio.gather(ns["end_chat"](1), ns["end_chat"](2))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertIsNone(await ns["end_chat"](1))
        db.save_chat.assert_awaited_once()

    async def test_missing_or_future_start_never_negative(self):
        for started in (None, "200"):
            redis, db, ns = self.setup_manager(started)
            await ns["end_chat"](1)
            db.save_chat.assert_awaited_once_with(1, 2, 0)

    async def test_database_failure_preserves_chat_for_retry(self):
        redis, db, ns = self.setup_manager()
        db.save_chat.side_effect = RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            await ns["end_chat"](1)
        self.assertEqual(await ns["get_partner"](1), 2)
        self.assertEqual(await redis.get("chat_started:1"), "100")

    async def test_both_ratings_do_not_save_or_overwrite_duration(self):
        notifications = types.ModuleType("services.notifications")
        notifications.send_post_chat_rating = AsyncMock()
        referrals = types.ModuleType("handlers.referral")
        referrals.activate_referral = AsyncMock()
        sys.modules["services.notifications"] = notifications
        sys.modules["handlers.referral"] = referrals
        ns = {"CallbackQuery": object, "_last_partner": {},
              "add_rating": AsyncMock(), "menu_kb": lambda: None}
        load_functions("rating.py", {"store_last_partner", "pop_last_partner", "cb_rate"}, ns)
        for user, partner in ((1, 2), (2, 1)):
            ns["store_last_partner"](user, partner)
            callback = types.SimpleNamespace(from_user=types.SimpleNamespace(id=user),
                data="rate:plus", bot=object(), answer=AsyncMock(),
                message=types.SimpleNamespace(edit_text=AsyncMock()))
            await ns["cb_rate"](callback)
            await ns["cb_rate"](callback)
        self.assertEqual(ns["add_rating"].await_count, 2)


if __name__ == "__main__":
    for path in ROOT.rglob('*.py'):
        compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
    unittest.main(verbosity=2)

