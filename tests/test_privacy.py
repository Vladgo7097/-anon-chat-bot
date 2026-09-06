import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from handlers.profile import cb_privacy
from services.result import ServiceResult


class PrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_screen_shows_saved_setting_and_toggle_updates_it(self):
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
        callback = SimpleNamespace(data="profile:privacy", from_user=SimpleNamespace(id=123),
                                   answer=AsyncMock(), message=message)
        user = SimpleNamespace(notification_settings='{"marketing": false}')
        with (
            patch("repositories.users.UserRepository.get", AsyncMock(return_value=user)),
            patch("services.profile.ProfileService.marketing",
                  AsyncMock(return_value=ServiceResult(True, "SAVED"))) as save,
        ):
            await cb_privacy(callback, state)
            self.assertIn("предложения: выключены", message.answer.await_args.args[0])
            keyboard = message.answer.await_args.kwargs["reply_markup"]
            self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "profile:push:on")
            self.assertEqual(keyboard.inline_keyboard[1][0].callback_data, "profile")
            save.assert_not_awaited()
            for action, enabled in (("profile:push:on", True), ("profile:push:off", False)):
                callback.data = action
                await cb_privacy(callback, state)
                save.assert_awaited_with(123, enabled)
                self.assertIn("предложения: " + ("включены" if enabled else "выключены"),
                              message.edit_text.await_args.args[0])
            message.edit_text.side_effect = TelegramBadRequest(
                method=EditMessageText(text="same"), message="Bad Request: message is not modified")
            await cb_privacy(callback, state)
            self.assertEqual(state.clear.await_count, 4)

    async def test_missing_profile_has_clear_response(self):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock())
        with patch("repositories.users.UserRepository.get", AsyncMock(return_value=None)):
            await cb_privacy(callback, SimpleNamespace(clear=AsyncMock()))
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
