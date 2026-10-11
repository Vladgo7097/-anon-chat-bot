"""Exercise worker delivery without Telegram or production data."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from workers import maintenance


class NotificationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def deliver(self, kind, text):
        notification = SimpleNamespace(id=1, user_id=123, type=kind, text=text,
                                       callback_data=None, attempt_count=1, broadcast_id=None)
        service = SimpleNamespace(schedule=AsyncMock(), expand_broadcast=AsyncMock(),
                                  progress_updates=AsyncMock(return_value=[]),
                                  drop_progress=AsyncMock(),
                                  next=AsyncMock(side_effect=[notification, asyncio.CancelledError()]),
                                  finish=AsyncMock(), broadcast_poll=AsyncMock(return_value=None))
        bot = SimpleNamespace(send_message=AsyncMock())
        with (
            patch.object(maintenance, "NotificationService", return_value=service),
            patch.object(maintenance.Path, "write_text"),
            patch("workers.maintenance.ChatService.reconcile", AsyncMock()),
            patch("workers.maintenance.asyncio.sleep", AsyncMock()),
            patch.object(maintenance.funnel_alert, "check", AsyncMock(return_value=None)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await maintenance.run(bot)
        service.finish.assert_awaited_once_with(1, "sent")
        bot.send_message.assert_awaited_once()
        return bot.send_message.await_args

    async def test_system_emoji_renders_and_dynamic_text_is_escaped(self):
        emoji = '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji>'
        for kind in ("match", "rating", "unban", "inactive", "reward"):
            request = await self.deliver(kind, emoji + ' Событие <лето> & друзья')
            self.assertEqual(request.kwargs["parse_mode"], "HTML")
            self.assertEqual(request.args[1], emoji + ' Событие &lt;лето&gt; &amp; друзья')

    async def test_admin_text_is_delivered_verbatim(self):
        for kind in ("broadcast", "admin_message", "ban"):
            text = '<b>Важно</b> 2 < 3 & 4 > 1'
            request = await self.deliver(kind, text)
            self.assertIsNone(request.kwargs["parse_mode"])
            self.assertEqual(request.args[1], text)

    async def test_poll_broadcast_uses_send_poll_not_send_message(self):
        notification = SimpleNamespace(id=1, user_id=123, type="broadcast", text="Нравится сайт?",
                                       callback_data=None, attempt_count=1, broadcast_id=7)
        service = SimpleNamespace(schedule=AsyncMock(), expand_broadcast=AsyncMock(),
                                  progress_updates=AsyncMock(return_value=[]),
                                  drop_progress=AsyncMock(),
                                  next=AsyncMock(side_effect=[notification, asyncio.CancelledError()]),
                                  finish=AsyncMock(),
                                  broadcast_poll=AsyncMock(return_value=(["Да", "Нет"], "Нравится сайт?")),
                                  record_poll=AsyncMock())
        sent_poll = SimpleNamespace(poll=SimpleNamespace(id="poll-xyz"))
        bot = SimpleNamespace(send_message=AsyncMock(), send_poll=AsyncMock(return_value=sent_poll))
        with (
            patch.object(maintenance, "NotificationService", return_value=service),
            patch.object(maintenance.Path, "write_text"),
            patch("workers.maintenance.ChatService.reconcile", AsyncMock()),
            patch("workers.maintenance.asyncio.sleep", AsyncMock()),
            patch.object(maintenance.funnel_alert, "check", AsyncMock(return_value=None)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await maintenance.run(bot)
        service.broadcast_poll.assert_awaited_once_with(7)
        bot.send_poll.assert_awaited_once_with(123, "Нравится сайт?", ["Да", "Нет"], is_anonymous=True)
        service.record_poll.assert_awaited_once_with("poll-xyz", 7, 2)
        bot.send_message.assert_not_awaited()
        service.finish.assert_awaited_once_with(1, "sent")
