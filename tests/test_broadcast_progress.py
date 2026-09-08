"""Broadcast progress: audience snapshot, live counters and the admin card."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import Broadcast, Notification
from services.admin import AdminService
from services.notifications import NotificationService
from utils.time import utcnow


class BroadcastProgressTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.ADMIN_IDS", {99})
        p.start()
        self.addCleanup(p.stop)

    async def launch(self, segment="all", content="Hello"):
        admin = AdminService()
        job = (await admin.broadcast(99, segment, content)).data["job"]
        result = await admin.confirm_broadcast(99, job.id)
        return admin, result.data["job"]

    async def test_audience_size_matches_what_expansion_actually_queues(self):
        async with self.factory.begin() as s:
            # One deleted and one who blocked the bot: both outside every segment.
            s.add(User(telegram_id=4, anon_id="#gone", gender="male", age=23,
                       onboarding_completed=True, deleted_at=utcnow()))
            s.add(User(telegram_id=5, anon_id="#blocked", gender="male", age=23,
                       onboarding_completed=True, bot_blocked_at=utcnow()))
            user = await s.scalar(select(User).where(User.telegram_id == 2))
            user.premium_expires = utcnow()+timedelta(days=1)
        admin = AdminService()
        self.assertEqual(await admin.audience_size(99, "all"), 3)
        self.assertEqual(await admin.audience_size(99, "premium"), 1)
        _, job = await self.launch()
        await NotificationService().expand_broadcast()
        async with self.factory() as s:
            queued = await s.scalar(select(func.count(Notification.id))
                                    .where(Notification.broadcast_id == job.id))
        self.assertEqual(queued, await admin.audience_size(99, "all"))

    async def test_confirm_stores_the_audience_snapshot(self):
        _, job = await self.launch()
        self.assertEqual(job.total, 3)
        async with self.factory() as s:
            self.assertEqual((await s.get(Broadcast, job.id)).total, 3)

    async def test_progress_counts_every_terminal_state(self):
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 2))
            user.notification_settings = '{"marketing":false}'
        admin, job = await self.launch()
        service = NotificationService()
        await service.expand_broadcast()
        first = await service.next()
        await service.finish(first.id, "sent")
        self.assertIsNone(await service.next())  # opted out, marked skipped
        third = await service.next()
        await service.finish(third.id, "failed", "forbidden")
        found = await admin.broadcast_progress(99, job.id)
        self.assertIsNotNone(found)
        stored, counts = found
        self.assertEqual((counts.get("sent"), counts.get("failed"), counts.get("skipped")), (1, 1, 1))
        from handlers.admin import broadcast_card
        card = broadcast_card(stored, counts)
        self.assertIn("100%", card)
        self.assertIn("Обработано: 3 из 3", card)

    async def test_progress_updates_are_throttled_and_stop_when_finished(self):
        admin, job = await self.launch()
        await admin.attach_progress(99, job.id, 500, 42)
        service = NotificationService()
        updates = await service.progress_updates()
        self.assertEqual([(u["chat_id"], u["message_id"], u["final"]) for u in updates], [(500, 42, False)])
        # A second pass within the refresh window must not touch Telegram again.
        self.assertEqual(await service.progress_updates(), [])
        await service.expand_broadcast()
        for _ in range(3):
            item = await service.next()
            if item:
                await service.finish(item.id, "sent")
        await service.expand_broadcast()
        async with self.factory.begin() as s:
            stored = await s.get(Broadcast, job.id)
            self.assertEqual(stored.status, "completed")
            stored.progress_at = utcnow()-timedelta(seconds=30)
        final = await service.progress_updates()
        self.assertTrue(final[0]["final"])
        # Keep tracking until Telegram confirms the final render.
        async with self.factory() as s:
            self.assertIsNotNone((await s.get(Broadcast, job.id)).progress_message_id)
        await service.drop_progress(job.id)
        async with self.factory.begin() as s:
            stored = await s.get(Broadcast, job.id)
            self.assertIsNone(stored.progress_message_id)
            stored.progress_at = utcnow()-timedelta(seconds=30)
        self.assertEqual(await service.progress_updates(), [])

    async def test_worker_renders_the_card_and_drops_a_deleted_message(self):
        from aiogram.exceptions import TelegramBadRequest
        from workers.maintenance import refresh_broadcast_progress
        admin, job = await self.launch()
        await admin.attach_progress(99, job.id, 500, 42)
        service = NotificationService()
        bot = SimpleNamespace(edit_message_text=AsyncMock())
        await refresh_broadcast_progress(bot, service)
        text = bot.edit_message_text.await_args.args[0]
        self.assertIn(f"Рассылка #{job.id}", text)
        self.assertIn("[", text)
        self.assertEqual(bot.edit_message_text.await_args.kwargs["message_id"], 42)
        async with self.factory.begin() as s:
            (await s.get(Broadcast, job.id)).progress_at = utcnow()-timedelta(seconds=30)
        bot.edit_message_text = AsyncMock(side_effect=TelegramBadRequest(
            method=SimpleNamespace(), message="message to edit not found"))
        await refresh_broadcast_progress(bot, service)
        async with self.factory() as s:
            self.assertIsNone((await s.get(Broadcast, job.id)).progress_message_id)


class BroadcastCardTests(unittest.TestCase):
    def test_bar_and_scale_never_exceed_the_snapshot(self):
        from handlers.admin import broadcast_card, progress_bar, THIN_SPACE
        self.assertEqual(progress_bar(0, 0), "░"*16)
        self.assertEqual(progress_bar(1, 2), "████████░░░░░░░░")
        self.assertEqual(progress_bar(9, 2), "████████████████")
        job = SimpleNamespace(id=1, segment="all", status="sending", total=1000, blocked=0,
                              started_at=None, finished_at=None)
        card = broadcast_card(job, {"sent": 250, "queued": 750})
        self.assertIn("25%", card)
        self.assertIn(f"Обработано: 250 из 1{THIN_SPACE}000", card)
        # An expansion larger than the snapshot must not report above 100%.
        job.total = 2
        self.assertIn("100%", broadcast_card(job, {"sent": 5}))

    def test_draft_has_no_progress_bar(self):
        from handlers.admin import broadcast_card
        job = SimpleNamespace(id=2, segment="premium", status="draft", total=0, blocked=0,
                              started_at=None, finished_at=None)
        card = broadcast_card(job, {})
        self.assertIn("Черновик", card)
        self.assertNotIn("%", card)

    def test_draft_list_entry_does_not_show_a_fake_pending_recipient(self):
        from keyboards.inline import admin_broadcast_status_kb
        job = SimpleNamespace(id=3, segment="nopay", status="draft", total=0, sent=0, failed=0)
        text = admin_broadcast_status_kb([job]).inline_keyboard[0][0].text
        self.assertIn("черновик", text)
        self.assertNotIn("0/1", text)

    def test_completed_list_entry_with_a_genuinely_empty_audience_shows_zero_of_zero(self):
        from keyboards.inline import admin_broadcast_status_kb
        job = SimpleNamespace(id=4, segment="nopay", status="completed", total=0, sent=0, failed=0)
        text = admin_broadcast_status_kb([job]).inline_keyboard[0][0].text
        self.assertIn("0/0", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ProgressTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_progress_is_acknowledged_only_after_delivery(self):
        from workers.maintenance import refresh_broadcast_progress
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import EditMessageText
        job = SimpleNamespace(id=1)
        service = SimpleNamespace(progress_updates=AsyncMock(return_value=[
            {"job": job, "chat_id": 99, "message_id": 1, "counts": {}, "final": True}]),
            drop_progress=AsyncMock(), defer_progress=AsyncMock())
        bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=TelegramRetryAfter(
            method=EditMessageText(chat_id=99, message_id=1, text="test"),
            message="rate limit", retry_after=10)))
        with patch("handlers.admin.broadcast_card", return_value="done"), patch("handlers.admin.broadcast_card_kb", return_value=None):
            await refresh_broadcast_progress(bot, service)
            service.drop_progress.assert_not_awaited()
            service.defer_progress.assert_awaited_once_with(1, 10)
            bot.edit_message_text.side_effect = None
            await refresh_broadcast_progress(bot, service)
        service.drop_progress.assert_awaited_once_with(1)

    async def test_retry_after_on_one_job_does_not_skip_the_rest_of_the_batch(self):
        from workers.maintenance import refresh_broadcast_progress
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.methods import EditMessageText
        job1, job2 = SimpleNamespace(id=1), SimpleNamespace(id=2)
        service = SimpleNamespace(progress_updates=AsyncMock(return_value=[
            {"job": job1, "chat_id": 99, "message_id": 1, "counts": {}, "final": False},
            {"job": job2, "chat_id": 99, "message_id": 2, "counts": {}, "final": True}]),
            drop_progress=AsyncMock(), defer_progress=AsyncMock())
        retry_error = TelegramRetryAfter(method=EditMessageText(chat_id=99, message_id=1, text="test"),
                                          message="rate limit", retry_after=10)
        bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=[retry_error, None]))
        with patch("handlers.admin.broadcast_card", return_value="done"), patch("handlers.admin.broadcast_card_kb", return_value=None):
            await refresh_broadcast_progress(bot, service)
        # job1 hit the rate limit and was deferred; job2 must still be
        # processed in the same pass instead of being abandoned.
        self.assertEqual(bot.edit_message_text.await_count, 2)
        service.defer_progress.assert_awaited_once_with(1, 10)
        service.drop_progress.assert_awaited_once_with(2)
