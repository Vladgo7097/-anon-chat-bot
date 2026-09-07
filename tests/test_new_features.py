import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.chat import forward_message
from handlers.reveal import cb_consent
from services.result import ServiceResult


class PrivacyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_contact_is_not_relayed_before_confirmation(self):
        bot = SimpleNamespace(copy_message=AsyncMock())
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=1),
            message_id=10, text="пиши @private_name", caption=None,
            content_type="text", bot=bot, answer=AsyncMock())
        relay = SimpleNamespace(
            SUPPORTED={"text"},
            prepare=AsyncMock(return_value=ServiceResult(True, "RELAY", {
                "partner": 2, "session_id": "session-1"})),
            contact_warning_required=AsyncMock(return_value=True))
        with patch("services.relay.RelayService", return_value=relay):
            await forward_message(message)
        bot.copy_message.assert_not_awaited()
        self.assertIn("найден контакт", message.answer.await_args.args[0])

    async def test_mutual_reveal_sends_partner_photo_privately(self):
        bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())
        callback = SimpleNamespace(
            data="v1:reveal:session-1", from_user=SimpleNamespace(id=2),
            bot=bot, answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        result = ServiceResult(True, "MUTUAL", {"profiles": {
            1: {"username": "one", "photo": "photo-one", "anon_id": "#ONE"},
            2: {"username": "two", "photo": None, "anon_id": "#TWO"}}, "partner": 1})
        with patch("services.reveal.RevealService.consent", AsyncMock(return_value=result)):
            await cb_consent(callback)
        bot.send_photo.assert_awaited_once()
        self.assertEqual(bot.send_photo.await_args.args[:2], (2, "photo-one"))
        bot.send_message.assert_awaited_once()

