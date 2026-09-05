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
                patch("handlers.start.get_or_create_user", new_callable=AsyncMock),
                patch("handlers.start.check_ban", new=AsyncMock(return_value=False)),
                patch("handlers.start.set_online", new_callable=AsyncMock),
                patch("handlers.streaks.check_and_update_streak",
                      new=AsyncMock(return_value={"reward": None})),
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
        finally:
            await bot.session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
