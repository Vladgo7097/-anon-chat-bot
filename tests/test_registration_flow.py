"""End-to-end registration through the real Dispatcher and the production
middleware chain: /start -> language -> gender -> age -> rules -> main menu.

Every previous onboarding test called OnboardingService directly, which is
exactly why a broken dispatch path (the language tap never reaching its
handler) stayed green. This file only exercises what a real user does: sends
messages and taps buttons.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from handlers import load_routers
from models.base import Base
from models.user import User as UserModel
import models.chat  # noqa: F401 -- registers metadata used by the routers
import models.operations  # noqa: F401
import models.payment  # noqa: F401
import models.report  # noqa: F401

from utils.middleware import AccessMiddleware, AntiFloodMiddleware

# aiogram routers are module-level singletons that refuse a second Dispatcher,
# and other test files build their own -- so every test here detaches the
# routers, builds a fresh Dispatcher with the production middleware chain, and
# detaches again on teardown. Each test registers its own fresh user id.
_BOT = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")
_BOT._me = User(id=1, is_bot=True, first_name="Test", username="offline_test_bot")

_next_uid = [9000]


class RegistrationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.uid = _next_uid[0]
        _next_uid[0] += 1
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "reg.db"))
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in ("models.base", "repositories.chats", "repositories.users",
                       "services.notifications", "services.growth", "services.payments"):
            p = patch(module + ".async_session", self.factory)
            p.start()
            self.addCleanup(p.stop)
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

        routers = load_routers()
        for router in routers:
            router._parent_router = None
        self.dp = Dispatcher()
        self.dp.include_routers(*routers)
        self.dp.message.outer_middleware(AccessMiddleware())
        self.dp.callback_query.outer_middleware(AccessMiddleware())
        self.dp.message.middleware(AntiFloodMiddleware())

        self.sent = []
        self.request = AsyncMock(side_effect=self._record)
        patch.object(_BOT.session, "make_request", self.request).start()
        for p in (
            patch("services.chat_manager.set_online", AsyncMock()),
            patch("services.chat_manager.get_partner", AsyncMock(return_value=None)),
            patch("utils.middleware.check_flood", AsyncMock(return_value=False)),
            patch("services.growth.StreakService.checkin", AsyncMock()),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.dp.storage.close()
        for router in load_routers():
            router._parent_router = None
        await self.engine.dispose()
        self.temp.cleanup()

    def _record(self, *args, **kwargs):
        # make_request(bot, method) -- args[1] is the outgoing Bot API method.
        payload = args[1]
        if getattr(payload, "__api_method__", None) == "sendMessage":
            self.sent.append(payload)
        return True

    async def _send_text(self, text, update_id=1):
        await self.dp.feed_update(_BOT, Update(update_id=update_id, message=Message(
            message_id=update_id, date=datetime.now(timezone.utc),
            chat=Chat(id=self.uid, type="private"),
            from_user=User(id=self.uid, is_bot=False, first_name="Tester"), text=text)))

    async def _tap(self, data, update_id=1):
        await self.dp.feed_update(_BOT, Update(update_id=update_id, callback_query=CallbackQuery(
            id=str(update_id), from_user=User(id=self.uid, is_bot=False, first_name="Tester"),
            chat_instance="offline", data=data, message=Message(
                message_id=update_id, date=datetime.now(timezone.utc),
                chat=Chat(id=self.uid, type="private"),
                from_user=User(id=1, is_bot=True, first_name="Test", username="offline_test_bot"),
                text="stub"))))

    async def _user(self):
        async with self.factory() as s:
            return await s.scalar(select(UserModel).where(UserModel.telegram_id == self.uid))

    async def _to_menu(self):
        """Drive a full RU registration and return the last sent payload."""
        await self._send_text("/start")
        await self._tap("lang:ru")
        await self._send_text("Парень")
        await self._send_text("25")
        await self._send_text("Принимаю правила")
        return self.sent[-1]

    async def test_full_registration_reaches_the_main_menu(self):
        await self._send_text("/start")
        self.assertIn("Выберите язык", self.sent[-1].text)

        await self._tap("lang:uk")
        user = await self._user()
        self.assertEqual(user.language, "uk", "язык не сохранился после тапа")
        # The welcome/gender screen must follow the language pick, in that language.
        self.assertIn("Вкажи свою стать", self.sent[-1].text)

        await self._send_text("Хлопець")
        user = await self._user()
        self.assertEqual(user.gender, "male")
        self.assertIn("Скільки тобі років", self.sent[-1].text)

        await self._send_text("23")
        user = await self._user()
        self.assertEqual(user.age, 23)
        self.assertIn("Правила спілкування", self.sent[-1].text)

        await self._send_text("Приймаю правила")
        user = await self._user()
        self.assertTrue(user.onboarding_completed)
        self.assertEqual(user.onboarding_step, "MENU")
        self.assertIn("Головне меню", self.sent[-1].text)

    async def test_language_tap_is_not_swallowed_by_middleware(self):
        """Regression: AccessMiddleware used to intercept every non-/start
        update while step != "MENU", so the `lang:*` callback never reached
        cb_language -- the bot re-sent the language screen forever."""
        await self._send_text("/start")
        before = len(self.sent)
        await self._tap("lang:en")
        self.assertEqual(len(self.sent), before + 1,
                         "тап по языку не дошёл до обработчика")
        self.assertNotIn("Выберите язык", self.sent[-1].text,
                         "вместо продолжения регистрации показан экран языка заново")
        self.assertEqual((await self._user()).language, "en")

    async def test_settings_language_change_still_works(self):
        await self._to_menu()
        self.assertIn("Главное меню", self.sent[-1].text)
        await self._tap("lang:uk")
        user = await self._user()
        self.assertEqual(user.language, "uk")
        self.assertIn("Мову змінено", self.sent[-2].text)
        self.assertIn("Головне меню", self.sent[-1].text)

    async def test_language_tap_on_a_two_day_old_message_still_works(self):
        """Telegram stops returning CallbackQuery.message ~48h after it was
        sent; answering the missing object raised, so the tap died with
        "Не удалось выполнить действие"."""
        await self._send_text("/start")
        await self.dp.feed_update(_BOT, Update(update_id=5, callback_query=CallbackQuery(
            id="5", from_user=User(id=self.uid, is_bot=False, first_name="Tester"),
            chat_instance="offline", data="lang:ru", message=None)))
        user = await self._user()
        self.assertEqual(user.language, "ru")
        self.assertIn("Укажи свой пол", self.sent[-1].text)

    async def test_typing_at_the_language_step_offers_the_screen_again(self):
        await self._send_text("/start")
        await self._send_text("просто текст")
        self.assertIn("Выберите язык", self.sent[-1].text)
        self.assertIsNone((await self._user()).language)


if __name__ == "__main__":
    unittest.main(verbosity=2)
