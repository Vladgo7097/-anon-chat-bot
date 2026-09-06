"""Search lifecycle checks without Telegram, Redis or production database IO."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import search
from services.result import ServiceResult
from test_chat_duration import DatabaseCase


class SearchRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        self.service = SimpleNamespace(
            enqueue=AsyncMock(return_value=ServiceResult(True, "ALREADY_SEARCHING")),
            cancel=AsyncMock(), find=AsyncMock(return_value=None))
        self.redis = AsyncMock()
        self.redis.get.return_value = str(time.time())
        self.redis.zscore.return_value = 1
        self.bot = SimpleNamespace(send_message=AsyncMock())
        for target, value in (
            ("handlers.search.MatchingService", lambda: self.service),
            ("handlers.search.get_redis", AsyncMock(return_value=self.redis)),
            ("handlers.search.get_online_count", AsyncMock(return_value=0)),
            ("services.settings.bot_value", AsyncMock(return_value=60)),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await search.shutdown_searches()

    async def waiting_loop(self, *args):
        await asyncio.Event().wait()

    async def test_repeat_search_restores_missing_task_without_duplicates(self):
        with patch.object(search, "search_loop", side_effect=self.waiting_loop) as loop:
            await asyncio.gather(search.begin_search(self.message, 1),
                                 search.begin_search(self.message, 1))
            self.assertEqual(loop.call_count, 1)
            self.assertEqual(list(search._tasks), [1])
            await search.cmd_cancel(self.message)
            self.assertEqual(search._tasks, {})
            self.service.cancel.assert_awaited_once_with(1)

    async def test_restart_restores_only_live_entries_once(self):
        async def entries(*args):
            for uid in ("1", "2", "1"):
                yield uid, 1

        self.redis.zscan_iter = entries
        self.redis.exists.side_effect = lambda key: key == "search:user:1"
        with patch.object(search, "search_loop", side_effect=self.waiting_loop) as loop:
            await search.restore_searches(self.bot)
            self.assertEqual(list(search._tasks), [1])
            loop.assert_called_once_with(None, 1, self.bot)
            self.service.cancel.assert_awaited_once_with(2)

    async def test_restart_keeps_original_timeout(self):
        self.redis.get.return_value = str(time.time() - 120)
        with patch("handlers.search.has_active_participation", AsyncMock(return_value=False)):
            await search.search_loop(None, 1, self.bot)
        self.service.find.assert_not_awaited()
        self.service.cancel.assert_awaited_once_with(1)
        self.bot.send_message.assert_awaited_once()
        self.assertIn("Пока никого нет", self.bot.send_message.await_args.args[1])

    async def test_timeout_after_concurrent_match_sends_no_failure(self):
        with patch("handlers.search.has_active_participation", AsyncMock(return_value=True)):
            await search.search_loop(None, 1, self.bot)
        self.service.cancel.assert_awaited_once_with(1)
        self.bot.send_message.assert_not_awaited()

    async def test_missing_lifetime_marker_never_matches(self):
        self.redis.get.return_value = None
        await search.search_loop(None, 1, self.bot)
        self.service.find.assert_not_awaited()
        self.service.cancel.assert_awaited_once_with(1)


class ExpiredQueueTests(DatabaseCase):
    async def test_expiry_of_either_participant_prevents_match(self):
        for expired in (1, 2):
            self.redis.exists.side_effect = lambda key: key != f"search:user:{expired}"
            self.assertIsNone(await self.repo.match(1, 2, self.redis))
        self.assertIsNone(await self.repo.active(1))
        self.assertIsNone(await self.repo.active(2))
