"""The community-chat menu button must actually reach its handler.

KeyboardButton has no url field in the Bot API: the first build put url= on
the reply button, Telegram dropped it silently, and a tap just sent the label
as plain text into the void -- the button "did nothing". The link now lives
in an inline answer, so this test feeds a real press through the dispatcher
and inspects what would go to Telegram.
"""
import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

import config
from handlers import load_routers
from keyboards import texts as T


class ChatLinkTests(unittest.TestCase):
    def test_every_localized_press_answers_with_inline_link(self):
        asyncio.run(self._run())

    async def _run(self):
        routers = load_routers()
        # Routers are module-level singletons: other dispatch tests attach
        # them without detaching, so drop any previous parent first and our
        # own attachment at the end -- include_router refuses a second one.
        for r in routers:
            r._parent_router = None
        dp = Dispatcher()
        dp.include_routers(*routers)
        bot = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")
        telegram = AsyncMock(return_value=True)
        labels = sorted(T.all_labels(T.CHAT))
        try:
            with patch.object(bot.session, "make_request", new=telegram):
                for index, label in enumerate(labels, start=100):
                    bot._me = User(id=123456, is_bot=True, first_name="Test",
                                   username="offline_test_bot")
                    await dp.feed_update(bot, Update(update_id=index, message=Message(
                        message_id=index,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=7, type="private"),
                        from_user=User(id=7, is_bot=False, first_name="T"),
                        text=label,
                    )))
                self.assertEqual(telegram.await_count, len(labels),
                                 "a chat-button press must be answered")
                for call in telegram.await_args_list:
                    request = call.args[1]
                    button = request.reply_markup.inline_keyboard[0][0]
                    self.assertEqual(button.url, config.COMMUNITY_CHAT_URL)
        finally:
            await bot.session.close()
            for r in routers:
                r._parent_router = None

    def test_invite_press_answers_with_referral_link(self):
        asyncio.run(self._run_invite())

    async def _run_invite(self):
        from types import SimpleNamespace

        class FakeSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def scalar(self, query):
                return SimpleNamespace(referral_code="ABC123", referrals_count=2,
                                       referrals_activated=1)

        routers = load_routers()
        for r in routers:
            r._parent_router = None
        dp = Dispatcher()
        dp.include_routers(*routers)
        bot = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")
        telegram = AsyncMock(return_value=True)
        labels = sorted(T.all_labels(T.INVITE))

        async def fake_request(*args, **kwargs):
            # make_request is called as (bot, method); answer getMe with a
            # real User so the handler can read bot.username.
            method = args[1] if len(args) > 1 else args[0]
            if type(method).__name__ == "GetMe":
                return User(id=1, is_bot=True, first_name="T",
                            username="offline_test_bot")
            return True

        telegram.side_effect = fake_request
        try:
            with (patch.object(bot.session, "make_request", new=telegram),
                  patch("models.base.async_session", lambda: FakeSession())):
                for index, label in enumerate(labels, start=200):
                    await dp.feed_update(bot, Update(update_id=index, message=Message(
                        message_id=index,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=7, type="private"),
                        from_user=User(id=7, is_bot=False, first_name="T"),
                        text=label,
                    )))
                sends = [c for c in telegram.await_args_list
                         if type(c.args[1]).__name__ == "SendMessage"]
                self.assertEqual(len(sends), len(labels),
                                 "an invite-button press must be answered")
                for call in sends:
                    request = call.args[1]
                    self.assertIn("ref_ABC123", request.text,
                                  "the card must carry the referral link")
                    share, rewards = request.reply_markup.inline_keyboard[0]
                    self.assertIn("offline_test_bot", share.url)
                    self.assertIn("ref_ABC123", share.url)
                    self.assertEqual(rewards.callback_data, "profile:rewards")
        finally:
            await bot.session.close()
            for r in routers:
                r._parent_router = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
