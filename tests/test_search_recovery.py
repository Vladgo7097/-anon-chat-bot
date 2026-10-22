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
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.message = SimpleNamespace(
            answer=AsyncMock(), from_user=SimpleNamespace(id=1), bot=self.bot
        )
        self.message.answer.return_value.delete = AsyncMock()
        self.service = SimpleNamespace(
            enqueue=AsyncMock(return_value=ServiceResult(True, "ALREADY_SEARCHING")),
            cancel=AsyncMock(),
            find=AsyncMock(return_value=None),
        )
        self.redis = AsyncMock()
        self.redis.get.return_value = str(time.time())
        self.redis.zscore.return_value = 1
        # notify_waiting_users() reads this set every time a search begins --
        # empty by default so it's a no-op unless a test opts in.
        self.redis.smembers.return_value = set()
        for target, value in (
            ("handlers.search.MatchingService", lambda: self.service),
            ("handlers.search.get_redis", AsyncMock(return_value=self.redis)),
            ("handlers.search.get_online_count", AsyncMock(return_value=0)),
            ("services.settings.bot_value", AsyncMock(return_value=60)),
            # Онлайн-счётчик в карточке поиска видит только владелец; по
            # умолчанию тесты ходят как обычные пользователи (OWNER_ID=0).
            ("config.OWNER_ID", 0),
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
            await asyncio.gather(
                search.begin_search(self.message, 1),
                search.begin_search(self.message, 1),
            )
            self.assertEqual(loop.call_count, 1)
            self.assertEqual(list(search._tasks), [1])
            await search.cmd_cancel(self.message)
            self.assertEqual(search._tasks, {})
            self.service.cancel.assert_awaited_once_with(1)

    async def test_typed_cancel_deletes_the_search_status_card(self):
        with patch.object(search, "search_loop", side_effect=self.waiting_loop):
            await search.begin_search(self.message, 1)
        await search.cmd_cancel(self.message)
        self.message.answer.return_value.delete.assert_awaited_once()
        self.assertNotIn(1, search._status_messages)

    async def test_inline_cancel_deletes_the_search_status_card(self):
        with patch.object(search, "search_loop", side_effect=self.waiting_loop):
            await search.begin_search(self.message, 1)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )
        await search.cb_cancel(callback)
        self.message.answer.return_value.delete.assert_awaited_once()
        self.assertNotIn(1, search._status_messages)

    async def test_cancel_without_an_active_search_does_not_try_to_delete_anything(
        self,
    ):
        await search.cmd_cancel(self.message)
        self.message.answer.return_value.delete.assert_not_awaited()

    async def test_start_status_hides_online_counter_from_users(self):
        with patch.object(search, "search_loop", side_effect=self.waiting_loop):
            await search.begin_search(self.message, 1)
        self.message.answer.assert_awaited_once()
        self.assertEqual(
            self.message.answer.await_args.kwargs["reply_markup"]
            .inline_keyboard[-1][0]
            .callback_data,
            "search:cancel",
        )
        self.assertNotIn("Онлайн сейчас", self.message.answer.await_args.args[0])

    async def test_start_status_shows_online_counter_to_owner_only(self):
        with (
            patch.object(search, "search_loop", side_effect=self.waiting_loop),
            patch("config.OWNER_ID", 1),
        ):
            await search.begin_search(self.message, 1)
        self.assertIn("Онлайн сейчас: 0", self.message.answer.await_args.args[0])

    async def test_callback_path_uses_the_tapper_id_not_the_bots(self):
        # callback.message.from_user — это бот; владелец определяется по
        # from_user колбэка, иначе счётчик был бы виден только при вводе /search.
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1), answer=AsyncMock(), message=self.message
        )
        with (
            patch.object(search, "search_loop", side_effect=self.waiting_loop),
            patch("config.OWNER_ID", 1),
        ):
            await search.cb_search_start(callback)
        self.assertIn("Онлайн сейчас: 0", self.message.answer.await_args.args[0])

    async def test_start_status_carries_custom_emoji_and_no_elapsed_timer(self):
        with patch.object(search, "search_loop", side_effect=self.waiting_loop):
            await search.begin_search(self.message, 1)
        text = self.message.answer.await_args.args[0]
        self.assertIn('<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji>', text)
        # У пользователей нет ни счётчика, ни его эмодзи.
        self.assertNotIn("Онлайн сейчас", text)
        self.assertNotIn("5215522595922779944", text)
        self.assertNotIn("Прошло", text)

    async def test_periodic_status_update_also_has_no_elapsed_timer(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        with (
            patch("handlers.premium.product_keyboard", AsyncMock(return_value=None)),
            patch("utils.db.is_premium", AsyncMock(return_value=False)),
            patch(
                "handlers.search.asyncio.sleep",
                AsyncMock(side_effect=asyncio.CancelledError),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await search.search_loop(status, 1, self.bot)
        text = status.edit_text.await_args.args[0]
        self.assertIn('<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji>', text)
        self.assertNotIn("Онлайн сейчас", text)
        self.assertNotIn("5215522595922779944", text)
        self.assertNotIn("Прошло", text)
        # The elapsed-timer's own custom emoji id must not linger either.
        self.assertNotIn("5258258882022612173", text)

    async def test_periodic_owner_update_keeps_the_online_counter(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        with (
            patch("handlers.premium.product_keyboard", AsyncMock(return_value=None)),
            patch("utils.db.is_premium", AsyncMock(return_value=False)),
            patch("config.OWNER_ID", 1),
            patch(
                "handlers.search.asyncio.sleep",
                AsyncMock(side_effect=asyncio.CancelledError),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await search.search_loop(status, 1, self.bot)
        text = status.edit_text.await_args.args[0]
        self.assertIn("Онлайн сейчас: 0", text)

    async def test_rejected_status_does_not_leave_orphan_search(self):
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import SendMessage

        self.message.answer.side_effect = TelegramRetryAfter(
            method=SendMessage(chat_id=1, text="test"),
            message="rate limit",
            retry_after=193,
        )
        with self.assertRaises(TelegramRetryAfter):
            await search.begin_search(self.message, 1)
        self.service.cancel.assert_awaited_once_with(1)
        self.assertNotIn(1, search._tasks)

    async def test_status_rate_limit_does_not_suspend_matching(self):
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import EditMessageText

        status = SimpleNamespace(
            bot=self.bot,
            edit_text=AsyncMock(
                side_effect=TelegramRetryAfter(
                    method=EditMessageText(chat_id=1, message_id=1, text="test"),
                    message="rate limit",
                    retry_after=193,
                )
            ),
        )
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
            await search.restore_searches()
            self.assertEqual(list(search._tasks), [1])
            # No bot is handed in: the loop resolves the sender from the row,
            # which is the only way to reach a search started on the other bot.
            loop.assert_called_once_with(None, 1)
            self.service.cancel.assert_awaited_once_with(2)

    async def test_restart_keeps_original_timeout(self):
        self.redis.get.return_value = str(time.time() - 120)
        with (
            patch(
                "handlers.search.has_active_participation",
                AsyncMock(return_value=False),
            ),
            patch("utils.db.is_premium", AsyncMock(return_value=False)),
        ):
            await search.search_loop(None, 1, self.bot)
        self.service.find.assert_not_awaited()
        self.service.cancel.assert_awaited_once_with(1)
        self.bot.send_message.assert_awaited_once()
        self.assertIn("Пока никого нет", self.bot.send_message.await_args.args[1])

    async def test_timeout_after_concurrent_match_sends_no_failure(self):
        with patch(
            "handlers.search.has_active_participation", AsyncMock(return_value=True)
        ):
            await search.search_loop(None, 1, self.bot)
        self.service.cancel.assert_awaited_once_with(1)
        self.bot.send_message.assert_not_awaited()

    async def test_timeout_ends_search_for_free_user(self):
        self.redis.get.return_value = str(time.time() - 120)
        with (
            patch(
                "handlers.search.has_active_participation",
                AsyncMock(return_value=False),
            ),
            patch("utils.db.is_premium", AsyncMock(return_value=False)),
        ):
            await search.search_loop(None, 1, self.bot)
        self.service.cancel.assert_awaited_once_with(1)
        self.service.enqueue.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def premium_loop(self, queued_seconds, settings):
        """Run one Premium search that has already waited queued_seconds."""
        self.service.enqueue = AsyncMock(
            return_value=ServiceResult(True, "SEARCH_STARTED")
        )
        self.service.find = AsyncMock(
            return_value=SimpleNamespace(user1_id=1, user2_id=2)
        )
        self.redis.get.return_value = str(time.time() - queued_seconds)
        with (
            patch(
                "services.settings.bot_value",
                AsyncMock(
                    side_effect=lambda key, default=None: settings.get(key, default)
                ),
            ),
            patch(
                "handlers.search.has_active_participation",
                AsyncMock(return_value=False),
            ),
            patch("utils.db.is_premium", AsyncMock(return_value=True)),
            patch("handlers.search.asyncio.sleep", AsyncMock()),
        ):
            await search.search_loop(None, 1, self.bot)

    async def test_premium_gets_another_window_while_under_the_cap(self):
        await self.premium_loop(
            70, {"search_timeout": 60, "premium_search_timeout": 180}
        )
        self.service.cancel.assert_awaited_once_with(1)
        self.service.enqueue.assert_awaited_once_with(1)
        self.service.find.assert_awaited_once_with(1)
        self.bot.send_message.assert_not_awaited()

    async def test_premium_stops_once_the_cap_is_reached(self):
        # Nobody should sit in the queue past the cap, Premium included.
        await self.premium_loop(
            200, {"search_timeout": 60, "premium_search_timeout": 180}
        )
        self.service.cancel.assert_awaited_once_with(1)
        self.service.enqueue.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()
        self.assertIn("Пока никого нет", self.bot.send_message.await_args.args[1])

    async def test_a_zero_cap_restores_the_unlimited_premium_search(self):
        await self.premium_loop(
            6000, {"search_timeout": 60, "premium_search_timeout": 0}
        )
        self.service.enqueue.assert_awaited_once_with(1)
        self.bot.send_message.assert_not_awaited()

    async def test_premium_search_notifies_when_reenqueue_rejected(self):
        # Still inside the cap, so the re-enqueue is attempted and refused.
        # The user paid for Premium and is left with a stale "searching" card
        # otherwise, with no explanation and no working button.
        self.service.enqueue = AsyncMock(return_value=ServiceResult(False, "BANNED"))
        self.redis.get.return_value = str(time.time() - 70)
        settings = {"search_timeout": 60, "premium_search_timeout": 180}
        with (
            patch(
                "services.settings.bot_value",
                AsyncMock(
                    side_effect=lambda key, default=None: settings.get(key, default)
                ),
            ),
            patch(
                "handlers.search.has_active_participation",
                AsyncMock(return_value=False),
            ),
            patch("utils.db.is_premium", AsyncMock(return_value=True)),
            patch("handlers.search.asyncio.sleep", AsyncMock()),
        ):
            await search.search_loop(None, 1, self.bot)
        self.service.enqueue.assert_awaited_once_with(1)
        self.bot.send_message.assert_awaited_once()
        self.assertIn("остановлен", self.bot.send_message.await_args.args[1])

    async def test_premium_search_stays_silent_when_reenqueue_finds_concurrent_match(
        self,
    ):
        # A concurrent match already sent its own notice to both sides;
        # sending a second, contradictory message here would be a bug too.
        self.service.enqueue = AsyncMock(
            return_value=ServiceResult(False, "ALREADY_CHATTING")
        )
        self.redis.get.return_value = str(time.time() - 70)
        settings = {"search_timeout": 60, "premium_search_timeout": 180}
        with (
            patch(
                "services.settings.bot_value",
                AsyncMock(
                    side_effect=lambda key, default=None: settings.get(key, default)
                ),
            ),
            patch(
                "handlers.search.has_active_participation",
                AsyncMock(return_value=False),
            ),
            patch("utils.db.is_premium", AsyncMock(return_value=True)),
            patch("handlers.search.asyncio.sleep", AsyncMock()),
        ):
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


class FakeLockRedis:
    """Just enough of redis-py's SET NX/EX and EVAL to test search_lock's CAS release."""

    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


class SearchLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeLockRedis()
        p = patch("handlers.search.get_redis", AsyncMock(return_value=self.redis))
        p.start()
        self.addCleanup(p.stop)

    async def test_lock_is_a_redis_key_with_nx_and_ttl(self):
        async with search.search_lock(42, ttl=10):
            pass
        key, value, nx, ex = self.redis.set_calls[0]
        self.assertEqual(key, "search:lock:42")
        self.assertTrue(nx)
        self.assertEqual(ex, 10)
        # Released on exit, so a later acquisition of the same key succeeds again.
        self.assertNotIn(key, self.redis.store)

    async def test_second_holder_waits_until_the_first_releases(self):
        order = []

        async def holder(name, delay):
            async with search.search_lock(1, retry_delay=0.001):
                order.append(f"{name}:enter")
                await asyncio.sleep(delay)
                order.append(f"{name}:exit")

        await asyncio.gather(holder("a", 0.02), holder("b", 0))
        # Whichever gets there first, the second holder's enter must not
        # appear before the first holder's exit.
        first = order[0].split(":")[0]
        self.assertEqual(
            order,
            [
                f"{first}:enter",
                f"{first}:exit",
                f"{'b' if first == 'a' else 'a'}:enter",
                f"{'b' if first == 'a' else 'a'}:exit",
            ],
        )

    async def test_release_does_not_delete_a_lock_it_no_longer_owns(self):
        # Simulates a holder whose TTL expired and got overwritten by a new
        # holder: releasing the stale token must not evict the new one.
        self.redis.store["search:lock:7"] = "someone-elses-token"
        released = await self.redis.eval(
            search._RELEASE_SCRIPT, 1, "search:lock:7", "my-token"
        )
        self.assertEqual(released, 0)
        self.assertEqual(self.redis.store["search:lock:7"], "someone-elses-token")


class FakeWaitingRedis:
    """Just enough of the redis set/exists/delete surface notify_waiting_users
    and cb_wait_for_match actually use."""

    def __init__(self):
        self.sets = {}
        self.strings = {}

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def srem(self, key, *members):
        self.sets.get(key, set()).difference_update(members)

    async def expire(self, key, ttl):
        pass

    async def set(self, key, value, ex=None, nx=False):
        self.strings[key] = value
        return True

    async def exists(self, key):
        return key in self.strings

    async def delete(self, key):
        self.strings.pop(key, None)


class WaitingListTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.redis = FakeWaitingRedis()
        p = patch("handlers.search.get_redis", AsyncMock(return_value=self.redis))
        p.start()
        self.addCleanup(p.stop)

    async def test_opting_in_saves_the_alert_and_confirms_in_the_users_language(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=2),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        await search.cb_wait_for_match(callback, lang="en")
        self.assertIn(2, {int(m) for m in self.redis.sets["search:waiting_alerts"]})
        self.assertTrue(await self.redis.exists("search:waiting_alert:2"))
        callback.message.edit_text.assert_awaited_once()
        self.assertIn("notify you", callback.message.edit_text.await_args.args[0])

    async def test_a_newcomer_wakes_an_opted_in_user_in_their_own_language(self):
        from sqlalchemy import select
        from models.user import User

        async with self.factory.begin() as s:
            user2 = await s.scalar(select(User).where(User.telegram_id == 2))
            user2.language = "uk"
        await self.redis.set("search:waiting_alert:2", "1")
        await self.redis.sadd("search:waiting_alerts", "2")
        bot = SimpleNamespace(send_message=AsyncMock())
        await search.notify_waiting_users(bot, newcomer_id=3)
        bot.send_message.assert_awaited_once()
        args = bot.send_message.await_args
        self.assertEqual(args.args[0], 2)
        self.assertIn("Хтось уже шукає", args.args[1])
        # The alert is one-shot: consumed once fired, not left to refire.
        self.assertEqual(self.redis.sets["search:waiting_alerts"], set())
        self.assertFalse(await self.redis.exists("search:waiting_alert:2"))

    async def test_the_newcomer_themself_is_never_notified(self):
        await self.redis.set("search:waiting_alert:1", "1")
        await self.redis.sadd("search:waiting_alerts", "1")
        bot = SimpleNamespace(send_message=AsyncMock())
        await search.notify_waiting_users(bot, newcomer_id=1)
        bot.send_message.assert_not_awaited()

    async def test_invite_link_uses_the_bots_own_username_not_a_hardcoded_one(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
            bot=SimpleNamespace(
                get_me=AsyncMock(
                    return_value=SimpleNamespace(username="totally_different_bot")
                )
            ),
        )
        await search.cb_invite_friend(callback, lang="ru")
        callback.message.answer.assert_awaited_once()
        text = callback.message.answer.await_args.args[0]
        self.assertIn("t.me/totally_different_bot?start=ref_", text)
