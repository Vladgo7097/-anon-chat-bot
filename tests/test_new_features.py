import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.chat import forward_message
from handlers.reveal import cb_consent, paid_reveal
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

    async def test_paid_reveal_distinguishes_not_paid_from_session_expired(self):
        message = SimpleNamespace(answer=AsyncMock())
        with patch("services.reveal.upgrade_request", AsyncMock(return_value=ServiceResult(False, "NOT_PAID"))):
            await paid_reveal(message, 1, "session-1", "reveal_priority_request")
        self.assertIn("Premium", message.answer.await_args.args[0])
        self.assertNotIn("завершён", message.answer.await_args.args[0])

        message = SimpleNamespace(answer=AsyncMock())
        with patch("services.reveal.upgrade_request", AsyncMock(return_value=ServiceResult(False, "SESSION_EXPIRED"))):
            await paid_reveal(message, 1, "session-1", "reveal_priority_request")
        self.assertIn("завершён", message.answer.await_args.args[0])


class RatingCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_already_edited_confirmation_does_not_crash_the_handler(self):
        from aiogram.exceptions import TelegramBadRequest
        from handlers.rating import cb_rate
        callback = SimpleNamespace(
            data="v1:rate:session-1:1", from_user=SimpleNamespace(id=1), answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(
                method=SimpleNamespace(), message="message to edit not found"))))
        with patch("repositories.chats.ChatRepository.rate", AsyncMock(return_value=True)):
            await cb_rate(callback)  # must not raise
        callback.answer.assert_awaited_once_with("Спасибо за оценку!")


class SettingsSaveTests(unittest.IsolatedAsyncioTestCase):
    def callback(self, data):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=1),
                                answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))

    async def test_active_chat_gets_its_own_message_not_a_premium_pitch(self):
        from handlers.settings import cb_save
        from services.result import ServiceResult
        with patch("services.settings.SettingsService.save_search",
                   AsyncMock(return_value=ServiceResult(False, "ACTIVE_CHAT"))):
            callback = self.callback("settings:gender:m")
            await cb_save(callback)
        text = callback.answer.await_args.args[0]
        self.assertIn("диалог", text)
        self.assertNotIn("Premium", text)
        callback.message.answer.assert_not_awaited()

    async def test_premium_required_still_offers_the_purchase_keyboard(self):
        from handlers.settings import cb_save
        from services.result import ServiceResult
        with (patch("services.settings.SettingsService.save_search",
                    AsyncMock(return_value=ServiceResult(False, "PREMIUM_REQUIRED"))),
              patch("handlers.premium.product_keyboard", AsyncMock(return_value=None))):
            callback = self.callback("settings:gender:m")
            await cb_save(callback)
        callback.message.answer.assert_awaited_once()

