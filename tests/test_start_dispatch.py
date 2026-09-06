"""Exercise real router registration and /start dispatch without external IO."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher
from aiogram.enums import MessageEntityType
from aiogram.types import Chat, Message, MessageEntity, Update, User

from handlers import load_routers
from services.result import ServiceResult


class StartDispatchTests(unittest.TestCase):
    def test_registered_handlers_reply_to_start(self):
        asyncio.run(self.check_dispatch())

    async def check_dispatch(self):
        routers = load_routers()
        for router in routers:
            self.assertTrue(
                any(observer.handlers for observer in router.observers.values()),
                f"Router {router.name} has no handlers",
            )
        before = [len(r.message.handlers) for r in routers]
        self.assertEqual(load_routers(), routers)
        self.assertEqual([len(r.message.handlers) for r in routers], before)

        dp = Dispatcher()
        dp.include_routers(*routers)
        bot = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")
        telegram = AsyncMock(return_value=True)
        try:
            with (
                patch("services.onboarding.OnboardingService.start",
                      new=AsyncMock(return_value=ServiceResult(True, "WELCOME"))),
                patch.object(bot.session, "make_request", new=telegram),
            ):
                for index, text in enumerate(("/start", "/start@offline_test_bot")):
                    update = Update(update_id=index, message=Message(
                        message_id=index + 1,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=123, type="private"),
                        from_user=User(id=123, is_bot=False, first_name="Test"),
                        text=text,
                        entities=[MessageEntity(type=MessageEntityType.BOT_COMMAND,
                                                offset=0, length=len(text))],
                    ))
                    bot._me = User(id=123456, is_bot=True, first_name="Test",
                                   username="offline_test_bot")
                    await dp.feed_update(bot, update)
                    from handlers.start import WELCOME_TEXT
                    request = telegram.await_args.args[1]
                    self.assertEqual(request.text, WELCOME_TEXT)
                    self.assertEqual(request.chat_id, 123)
                self.assertEqual(telegram.await_count, 2)
            from handlers.profile import ProfileEdit
            from keyboards import texts as T
            state=dp.fsm.get_context(bot=bot,chat_id=123,user_id=123)
            await state.set_state(ProfileEdit.GENDER)
            with patch("services.profile.ProfileService.edit",AsyncMock(return_value=ServiceResult(True,"SAVED"))) as edit, patch("handlers.profile.show_profile",AsyncMock()), patch.object(bot.session,"make_request",telegram):
                await dp.feed_update(bot,Update(update_id=3,message=Message(
                    message_id=3,date=datetime.now(timezone.utc),chat=Chat(id=123,type="private"),
                    from_user=User(id=123,is_bot=False,first_name="Test"),text=T.MALE)))
                edit.assert_awaited_once_with(123,"gender","male")
                self.assertIsNone(await state.get_state())
            from handlers.help import HELP_TEXT
            from handlers.stats import format_stats
            stats = {"chats_count": 7, "total_chat_seconds": 3660, "rating_pct": 80}
            with (
                patch("config.ADMIN_IDS", {99}),
                patch("handlers.stats.get_user_stats", AsyncMock(return_value=stats)) as get_stats,
                patch("services.admin.AdminService.find_user", AsyncMock(return_value=None)) as find_user,
                patch.object(bot.session, "make_request", telegram),
            ):
                for user_id in (123, 99):
                    for text, expected in ((T.STATS, format_stats(stats)), (T.HELP, HELP_TEXT)):
                        telegram.reset_mock()
                        await dp.feed_update(bot, Update(update_id=10, message=Message(
                            message_id=10, date=datetime.now(timezone.utc),
                            chat=Chat(id=user_id, type="private"),
                            from_user=User(id=user_id, is_bot=False, first_name="Test"),
                            text=text,
                        )))
                        telegram.assert_awaited_once()
                        self.assertEqual(telegram.await_args.args[1].text, expected)
                        self.assertEqual(telegram.await_args.args[1].chat_id, user_id)
                self.assertEqual(get_stats.await_count, 2)
                find_user.assert_not_awaited()
                for text in ("123", "#ABC123"):
                    await dp.feed_update(bot, Update(update_id=11, message=Message(
                        message_id=11, date=datetime.now(timezone.utc),
                        chat=Chat(id=99, type="private"),
                        from_user=User(id=99, is_bot=False, first_name="Admin"), text=text,
                    )))
                    find_user.assert_awaited_with(99, text.lstrip("#"))
        finally:
            await bot.session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
