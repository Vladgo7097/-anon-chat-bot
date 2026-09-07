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

    async def test_start_status_contains_online_in_one_message(self):
        with patch.object(search, "search_loop", side_effect=self.waiting_loop):
            await search.begin_search(self.message, 1)
        self.message.answer.assert_awaited_once()
        self.assertEqual(self.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[-1][0].callback_data, "search:cancel")
        self.assertIn("Онлайн сейчас: 0", self.message.answer.await_args.args[0])

    async def test_rejected_status_does_not_leave_orphan_search(self):
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import SendMessage
        self.message.answer.side_effect = TelegramRetryAfter(
            method=SendMessage(chat_id=1, text="test"), message="rate limit", retry_after=193)
        with self.assertRaises(TelegramRetryAfter):
            await search.begin_search(self.message, 1)
        self.service.cancel.assert_awaited_once_with(1)
        self.assertNotIn(1, search._tasks)

    async def test_status_rate_limit_does_not_suspend_matching(self):
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import EditMessageText
        status = SimpleNamespace(bot=self.bot, edit_text=AsyncMock(side_effect=TelegramRetryAfter(
            method=EditMessageText(chat_id=1, message_id=1, text="test"),
            message="rate limit", retry_after=193)))
        self.service.find.side_effect = [None, SimpleNamespace(user1_id=1, user2_id=2)]
        with patch("handlers.search.asyncio.sleep", AsyncMock()) as sleep:
            await search.search_loop(status, 1)
        self.assertEqual(self.service.find.await_count, 2)
        self.assertTrue(all(call.args[0] <= 2 for call in sleep.await_args_list))
        self.service.cancel.assert_not_awaited()

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
        with patch("handlers.search.has_active_participation", AsyncMock(return_value=False)), \
             patch("utils.db.is_premium", AsyncMock(return_value=False)):
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

    async def test_timeout_ends_search_for_free_user(self):
        self.redis.get.return_value = str(time.time() - 120)
        with patch("handlers.search.has_active_participation", AsyncMock(return_value=False)), \
             patch("utils.db.is_premium", AsyncMock(return_value=False)):
            await search.search_loop(None, 1, self.bot)
        self.service.cancel.assert_awaited_once_with(1)
        self.service.enqueue.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def premium_loop(self, queued_seconds, settings):
        """Run one Premium search that has already waited queued_seconds."""
        self.service.enqueue = AsyncMock(return_value=ServiceResult(True, "SEARCH_STARTED"))
        self.service.find = AsyncMock(return_value=SimpleNamespace(user1_id=1, user2_id=2))
        self.redis.get.return_value = str(time.time() - queued_seconds)
        with patch("services.settings.bot_value", AsyncMock(side_effect=lambda key, default=None: settings.get(key, default))), \
             patch("handlers.search.has_active_participation", AsyncMock(return_value=False)), \
             patch("utils.db.is_premium", AsyncMock(return_value=True)), \
             patch("handlers.search.asyncio.sleep", AsyncMock()):
            await search.search_loop(None, 1, self.bot)

    async def test_premium_gets_another_window_while_under_the_cap(self):
        await self.premium_loop(70, {"search_timeout": 60, "premium_search_timeout": 180})
        self.service.cancel.assert_awaited_once_with(1)
        self.service.enqueue.assert_awaited_once_with(1)
        self.service.find.assert_awaited_once_with(1)
        self.bot.send_message.assert_not_awaited()

    async def test_premium_stops_once_the_cap_is_reached(self):
        # Nobody should sit in the queue past the cap, Premium included.
        await self.premium_loop(200, {"search_timeout": 60, "premium_search_timeout": 180})
        self.service.cancel.assert_awaited_once_with(1)
        self.service.enqueue.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.assertIn("Пока никого нет", self.bot.send_message.await_args.args[1])

    async def test_a_zero_cap_restores_the_unlimited_premium_search(self):
        await self.premium_loop(6000, {"search_timeout": 60, "premium_search_timeout": 0})
        self.service.enqueue.assert_awaited_once_with(1)
        self.bot.send_message.assert_not_awaited()

    async def test_premium_search_exits_when_reenqueue_rejected(self):
        # Still inside the cap, so the re-enqueue is attempted and refused.
        self.service.enqueue = AsyncMock(return_value=ServiceResult(False, "BANNED"))
        self.redis.get.return_value = str(time.time() - 70)
        settings = {"search_timeout": 60, "premium_search_timeout": 180}
        with patch("services.settings.bot_value", AsyncMock(side_effect=lambda key, default=None: settings.get(key, default))), \
             patch("handlers.search.has_active_participation", AsyncMock(return_value=False)), \
             patch("utils.db.is_premium", AsyncMock(return_value=True)), \
             patch("handlers.search.asyncio.sleep", AsyncMock()):
            await search.search_loop(None, 1, self.bot)
        self.service.enqueue.assert_awaited_once_with(1)
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
