import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select, event
from redis.exceptions import ConnectionError
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import CopyMessage
from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import Notification
from services.notifications import NotificationService
from repositories.users import UserRepository
from repositories.chats import compatible
from utils.time import utcnow


class AuditDatabaseTests(DatabaseCase):
    async def test_reported_partner_is_excluded_from_next_match(self):
        from services.reports import ReportService
        chat = await self.repo.match(1, 2, self.redis)
        with patch("services.chat_manager.get_redis", AsyncMock(return_value=self.redis)):
            self.assertTrue((await ReportService().submit(1, chat.session_id, "spam")).ok)
        self.assertIsNone(await self.repo.match(1, 2, self.redis))

    async def test_scheduler_does_not_acquire_matching_lock_and_dedupes(self):
        async with self.factory.begin() as s:
            for user in (await s.scalars(select(User))).all():
                user.last_online = utcnow()-timedelta(days=4)
        with patch("repositories.chats.lock_matching", side_effect=AssertionError("matching lock used")):
            await NotificationService().schedule()
            await NotificationService().schedule()
        async with self.factory() as s:
            notices = (await s.scalars(select(Notification).where(Notification.type == "inactive"))).all()
            self.assertEqual(len(notices), 3)

    async def test_repeated_profile_update_does_not_write_users(self):
        await UserRepository().get_or_create(1, "same", "Same")
        writes = []
        def observe(conn, cursor, statement, parameters, context, many):
            if statement.upper().startswith("UPDATE USERS"):
                writes.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", observe)
        try:
            await UserRepository().get_or_create(1, "same", "Same")
            await UserRepository().get_or_create(1, "same", "Same")
            self.assertEqual(writes, [])
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", observe)

    async def test_candidate_prefilter_skips_incompatible_without_matching_lock(self):
        async with self.factory.begin() as s:
            own = await s.scalar(select(User).where(User.telegram_id == 1))
            own.settings_gender_filter = "f"
            own.premium_expires = utcnow()+timedelta(days=1)
        with patch("repositories.chats.lock_matching", side_effect=AssertionError("matching lock used")):
            self.assertEqual(await self.repo.compatible_candidates(1, [3, 2]), [2])
        async with self.factory() as s:
            a, b = (await s.scalars(select(User).where(User.telegram_id.in_([1, 2])).order_by(User.telegram_id))).all()
            b.age = None
            self.assertFalse(compatible(a, b))


class AuditTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_update_is_ignored_and_failure_can_retry(self):
        from utils.deduplication import UpdateDeduplication
        saved = set()
        redis = AsyncMock()
        redis.exists.side_effect = lambda key: key in saved
        redis.set.side_effect = lambda key, value, **kwargs: saved.add(key)
        handler = AsyncMock()
        data = {"bot": SimpleNamespace(id=1)}
        with patch("utils.deduplication.get_redis", AsyncMock(return_value=redis)):
            middleware = UpdateDeduplication()
            await asyncio.gather(*(middleware(handler, SimpleNamespace(update_id=1), data) for _ in range(3)))
            handler.assert_awaited_once()
            # A new instance represents a restarted polling process.
            await UpdateDeduplication()(handler, SimpleNamespace(update_id=1), data)
            handler.assert_awaited_once()
            handler.side_effect = RuntimeError("failed")
            with self.assertRaises(RuntimeError):
                await middleware(handler, SimpleNamespace(update_id=2), data)
            handler.side_effect = None
            await middleware(handler, SimpleNamespace(update_id=2), data)
            self.assertIn("update:done:1:2", saved)

    async def test_invoice_transport_uses_stars(self):
        from handlers.premium import cb_buy
        from services.result import ServiceResult
        payment = SimpleNamespace(id=1, payload="test", stars_amount=100, product="premium_1m")
        bot = SimpleNamespace(send_invoice=AsyncMock(return_value=SimpleNamespace(message_id=5)))
        callback = SimpleNamespace(data="v1:buy:premium_1m", from_user=SimpleNamespace(id=1),
                                   answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()), bot=bot)
        with patch("services.payments.PaymentService.create", AsyncMock(return_value=ServiceResult(True, "INVOICE", {"payment": payment, "title": "Premium"}))), \
             patch("services.payments.PaymentService.claim_invoice", AsyncMock(return_value=True)), \
             patch("services.payments.PaymentService.invoice_sent", AsyncMock()):
            await cb_buy(callback)
        self.assertEqual(bot.send_invoice.await_args.kwargs["currency"], "XTR")
        self.assertEqual(bot.send_invoice.await_args.kwargs["prices"][0].amount, 100)

    async def test_album_middleware_calls_flood_and_relay_once_per_group(self):
        from utils.albums import AlbumMiddleware
        from handlers.chat import forward_message
        ready, release = asyncio.Event(), asyncio.Event()
        async def collect(seconds):
            ready.set()
            await release.wait()
        middleware = AlbumMiddleware()
        handler = AsyncMock()
        first = SimpleNamespace(chat=SimpleNamespace(id=1), media_group_id="a", message_id=10)
        second = SimpleNamespace(chat=SimpleNamespace(id=1), media_group_id="a", message_id=11)
        data = {"handler": SimpleNamespace(callback=forward_message)}
        with patch("utils.albums.asyncio.sleep", collect):
            task = asyncio.create_task(middleware(handler, first, data))
            await ready.wait()
            await middleware(handler, second, dict(data))
            release.set()
            await task
        handler.assert_awaited_once()
        self.assertEqual([m.message_id for m in handler.await_args.args[1]["album"]], [10, 11])
        self.assertEqual(middleware.pending, {})

    async def test_late_item_arriving_while_the_handler_is_still_running_is_not_dropped(self):
        # forward_message (copy_messages, DB writes) can outlast the 0.7s
        # collection window; a straggler for the same group must start its
        # own album instead of landing in the already-handed-off list.
        from utils.albums import AlbumMiddleware
        from handlers.chat import forward_message
        sleep_ready, sleep_release = asyncio.Event(), asyncio.Event()

        async def collect(seconds):
            sleep_ready.set()
            await sleep_release.wait()

        handler_started, handler_release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(event, data):
            calls.append([m.message_id for m in data["album"]])
            handler_started.set()
            await handler_release.wait()

        middleware = AlbumMiddleware()
        data = {"handler": SimpleNamespace(callback=forward_message)}
        first = SimpleNamespace(chat=SimpleNamespace(id=1), media_group_id="a", message_id=10)
        late = SimpleNamespace(chat=SimpleNamespace(id=1), media_group_id="a", message_id=99)

        with patch("utils.albums.asyncio.sleep", collect):
            task1 = asyncio.create_task(middleware(handler, first, dict(data)))
            await sleep_ready.wait()
            sleep_release.set()
            await handler_started.wait()  # handler(first) is now "still processing"
            sleep_ready.clear()
            sleep_release.clear()
            handler_started.clear()

            task2 = asyncio.create_task(middleware(handler, late, dict(data)))
            # Bounded waits: the pre-fix code returns task2 immediately without
            # ever calling the handler, which would otherwise hang this test
            # forever instead of failing it.
            await asyncio.wait_for(sleep_ready.wait(), timeout=2)
            sleep_release.set()
            await asyncio.wait_for(handler_started.wait(), timeout=2)  # handler(late) reached, not swallowed

            handler_release.set()
            await asyncio.wait_for(asyncio.gather(task1, task2), timeout=2)

        self.assertEqual(sorted(calls), [[10], [99]])

    @staticmethod
    def _queue_redis(pages, alive_per_page):
        """redis mock whose pipeline() is synchronous, like redis-py."""
        redis = AsyncMock()
        redis.zrange.side_effect = pages
        pipes = []
        for alive in alive_per_page:
            pipe = AsyncMock()
            pipe.__aenter__.return_value = pipe
            pipe.__aexit__.return_value = False
            pipe.exists = MagicMock()
            pipe.execute = AsyncMock(return_value=alive)
            pipes.append(pipe)
        redis.pipeline = MagicMock(side_effect=pipes)
        return redis

    async def test_queue_scans_beyond_first_page(self):
        from services.matching import MatchingService
        first = [str(n) for n in range(1, 201)]
        redis = self._queue_redis([first, ["201"]], [[1]*200, [1]])
        repo = SimpleNamespace(compatible_candidates=AsyncMock(side_effect=[[], [201]]),
                               match=AsyncMock(return_value=SimpleNamespace(session_id="s")))
        with patch("services.chat_manager.get_redis", AsyncMock(return_value=redis)), \
             patch("services.matching.ChatRepository", return_value=repo), \
             patch("services.chats.ChatService.sync", AsyncMock()):
            self.assertIsNotNone(await MatchingService().find(1))
        repo.match.assert_awaited_once_with(1, 201, redis)
        redis.zrange.assert_awaited_with("search_queue", 200, 399)

    async def test_expired_queue_entries_are_dropped_and_do_not_shift_paging(self):
        from services.matching import MatchingService
        first = [str(n) for n in range(1, 201)]
        alive = [0 if value in {"5", "7"} else 1 for value in first]
        redis = self._queue_redis([first, ["201"]], [alive, [1]])
        repo = SimpleNamespace(compatible_candidates=AsyncMock(side_effect=[[], [201]]),
                               match=AsyncMock(return_value=SimpleNamespace(session_id="s")))
        with patch("services.chat_manager.get_redis", AsyncMock(return_value=redis)), \
             patch("services.matching.ChatRepository", return_value=repo), \
             patch("services.chats.ChatService.sync", AsyncMock()):
            self.assertIsNotNone(await MatchingService().find(1))
        redis.zrem.assert_awaited_once_with("search_queue", "5", "7")
        # Dead entries never reach the database pre-filter.
        self.assertNotIn(5, repo.compatible_candidates.await_args_list[0].args[1])
        # Two entries left the queue, so the tail moved two positions down.
        redis.zrange.assert_awaited_with("search_queue", 198, 397)

    async def test_redis_outage_preserves_noncritical_functions_and_state_read(self):
        from services.chat_manager import set_online, get_online_count, check_flood
        from utils.storage import AvailableRedisStorage
        redis = AsyncMock()
        redis.get.side_effect = ConnectionError("offline")
        with patch("services.chat_manager.get_redis", AsyncMock(side_effect=ConnectionError("offline"))):
            self.assertIsNone(await set_online(1))
            self.assertEqual(await get_online_count(), "—")
            self.assertFalse(await check_flood(1))
        from aiogram.fsm.storage.base import StorageKey
        self.assertIsNone(await AvailableRedisStorage(redis).get_state(StorageKey(bot_id=1, chat_id=1, user_id=1)))

    async def test_retry_after_retries_only_transport(self):
        from utils.telegram import RetryTelegramRequests
        method = CopyMessage(chat_id=2, from_chat_id=1, message_id=3)
        request = AsyncMock(side_effect=[TelegramRetryAfter(method=method, message="limit", retry_after=1), "ok"])
        with patch("utils.telegram.asyncio.sleep", AsyncMock()) as sleep:
            self.assertEqual(await RetryTelegramRequests()(request, "bot", method), "ok")
            sleep.assert_awaited_once_with(1)
        self.assertEqual(request.await_count, 2)
