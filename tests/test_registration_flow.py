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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from handlers import load_routers
from models.base import Base
from models.user import User as UserModel
from services.result import ServiceResult
import models.chat  # noqa: F401 -- registers metadata used by the routers
import models.operations  # noqa: F401
import models.payment  # noqa: F401
import models.report  # noqa: F401
import models.start  # noqa: F401
import models.op  # noqa: F401

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
        # Patch every module that holds its own reference to async_session:
        # a consumer imported while another test's patch is active would keep
        # that test's factory (and its deleted temp file) forever.
        for module in ("models.base", "repositories.chats", "repositories.users",
                       "services.notifications", "services.registration",
                       "services.growth", "services.payments",
                       "services.profile", "services.matching",
                       "services.settings", "services.reveal", "services.reports",
                       "services.admin", "services.ads", "services.op"):
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

    async def _send_text(self, text, update_id=1, lang_code=None):
        await self.dp.feed_update(_BOT, Update(update_id=update_id, message=Message(
            message_id=update_id, date=datetime.now(timezone.utc),
            chat=Chat(id=self.uid, type="private"),
            from_user=User(id=self.uid, is_bot=False, first_name="Tester",
                            language_code=lang_code), text=text)))

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

    async def _seed_user(self, **fields):
        async with self.factory.begin() as s:
            s.add(UserModel(telegram_id=self.uid, anon_id=f"#seed{self.uid}", **fields))

    async def test_age_gate_boundaries_and_stray_whitespace(self):
        await self._send_text("/start")
        await self._tap("lang:ru")
        await self._send_text("Парень")
        for age in ("17", "100", "0"):
            await self._send_text(age)
            self.assertIn("Сервис доступен только с 18 лет", self.sent[-1].text,
                          f"возраст {age!r} должен быть отклонён сервером")
            self.assertIsNone((await self._user()).age)
        # Nonsense on the only step the user can type into must say so, not
        # silently re-show the keyboard.
        await self._send_text("abc")
        self.assertIn("Введи возраст кнопкой", self.sent[-1].text)
        # Telegram Web appends a NBSP to typed digits -- the tap must still count.
        await self._send_text(" 25 ")
        self.assertEqual((await self._user()).age, 25)
        self.assertIn("Правила", self.sent[-1].text)

    async def test_interrupted_registration_resumes_where_it_stopped(self):
        await self._seed_user(language="uk", gender="female", onboarding_step="AGE")
        await self._send_text("/start")
        self.assertIn("Скільки тобі років", self.sent[-1].text)
        self.assertNotIn("Choose your language", self.sent[-1].text)
        await self._send_text("21")
        self.assertIn("Правила спілкування", self.sent[-1].text)
        await self._send_text("Приймаю правила")
        self.assertTrue((await self._user()).onboarding_completed)
        self.assertIn("Головне меню", self.sent[-1].text)

    async def test_double_language_tap_does_not_jump_to_the_menu(self):
        await self._send_text("/start")
        await self._tap("lang:ru")
        await self._tap("lang:uk")
        user = await self._user()
        self.assertEqual(user.language, "uk")
        self.assertFalse(user.onboarding_completed)
        self.assertIn("Вкажи свою стать", self.sent[-1].text)
        self.assertNotIn("Головне меню", self.sent[-1].text)

    async def test_menu_tap_mid_registration_does_not_fake_the_menu(self):
        await self._send_text("/start")
        await self._tap("lang:ru")
        await self._tap("menu")
        user = await self._user()
        self.assertFalse(user.onboarding_completed)
        self.assertIn("Укажи свой пол", self.sent[-1].text)

    async def test_age_pages_and_rules_view(self):
        await self._send_text("/start")
        await self._tap("lang:ru")
        await self._send_text("Парень")
        self.assertIn("18", self.sent[-1].reply_markup.keyboard[0][0].text)
        await self._send_text("Дальше")
        self.assertIn("30", self.sent[-1].reply_markup.keyboard[0][0].text)
        await self._send_text("Назад")
        self.assertIn("18", self.sent[-1].reply_markup.keyboard[0][0].text)
        await self._send_text("25")
        self.assertIn("Правила общения", self.sent[-1].text)
        await self._send_text("Читать правила полностью")
        # The rules arrive as an extra message; the step in progress stays put.
        self.assertTrue(any("Правила общения" in m.text for m in self.sent[-2:]))
        self.assertIn("Принимаю", self.sent[-1].reply_markup.keyboard[0][0].text)
        user = await self._user()
        self.assertIsNone(user.rules_accepted_at, "просмотр правил не должен их принимать")
        await self._send_text("Принимаю правила")
        self.assertTrue((await self._user()).onboarding_completed)

    async def test_unregistered_user_cannot_reach_menu_features(self):
        from handlers.profile import show_profile
        from handlers.search import begin_search
        with (
            patch("handlers.profile.show_profile", AsyncMock()) as profile,
            patch("handlers.search.begin_search", AsyncMock()) as search,
        ):
            await self._send_text("/start")
            await self._tap("lang:ru")
            # Every menu affordance must bounce back to the unfinished step.
            for action in ("Найти собеседника", "Мой профиль", "Настройки поиска", "Premium"):
                await self._send_text(action)
                self.assertIn("Укажи свой пол", self.sent[-1].text,
                              f"{action!r} должен вернуть на шаг регистрации")
            for callback in ("search:start", "profile", "settings", "premium"):
                await self._tap(callback)
                self.assertIn("Укажи свой пол", self.sent[-1].text,
                              f"{callback!r} должен вернуть на шаг регистрации")
            profile.assert_not_awaited()
            search.assert_not_awaited()
        self.assertFalse((await self._user()).onboarding_completed)

    async def test_re_registration_after_account_deletion(self):
        from services.profile import ProfileService
        await self._to_menu()
        with (
            patch("services.matching.MatchingService.cancel", AsyncMock(return_value=ServiceResult(True, "OK"))),
            patch("services.chats.ChatService.end_chat", AsyncMock(return_value=ServiceResult(True, "OK"))),
        ):
            self.assertTrue((await ProfileService().delete(self.uid)).ok)
        await self._send_text("/start")
        # The language survives the deletion, so the flow restarts at gender,
        # not at the language screen the user already answered once.
        self.assertIn("Укажи свой пол", self.sent[-1].text)
        await self._send_text("Девушка")
        await self._send_text("31")
        await self._send_text("Принимаю правила")
        user = await self._user()
        self.assertTrue(user.onboarding_completed)
        self.assertIsNone(user.deleted_at)
        self.assertIn("Главное меню", self.sent[-1].text)

    async def test_banned_user_cannot_walk_through_registration(self):
        await self._seed_user(language="ru", is_banned=True, onboarding_step="WELCOME")
        await self._send_text("/start")
        self.assertIn("Ты заблокирован", self.sent[-1].text)
        user = await self._user()
        self.assertFalse(user.onboarding_completed)

    async def test_referral_payload_is_stored(self):
        from models.operations import Referral
        from utils.time import utcnow
        referrer_id = self.uid + 500
        async with self.factory.begin() as s:
            s.add(UserModel(telegram_id=referrer_id, anon_id=f"#ref{referrer_id}",
                            referral_code="refcode123", gender="male", age=30,
                            onboarding_completed=True, onboarding_step="MENU",
                            rules_version=1, rules_accepted_at=utcnow()))
        await self._send_text("/start ref_refcode123")
        user = await self._user()
        self.assertEqual(user.referrer_id, referrer_id, "реферальная ссылка не записана")
        async with self.factory() as s:
            referral = await s.scalar(select(Referral).where(Referral.referred_id == self.uid))
        self.assertIsNotNone(referral, "строка реферала не создана")
        self.assertIn("Выберите язык", self.sent[-1].text)

    async def test_start_during_an_active_chat_is_redirected(self):
        await self._to_menu()
        with patch("services.chat_manager.get_partner", AsyncMock(return_value=456)):
            await self._send_text("/start")
        self.assertIn("Ты сейчас в диалоге", self.sent[-1].text)

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

    async def test_client_language_skips_the_picker(self):
        """A Ukrainian client is a Ukrainian user: no picker tap, nothing to
        stall on -- the language bug lived on that very tap."""
        await self._send_text("/start", lang_code="uk")
        self.assertNotIn("Choose your language", self.sent[-1].text)
        self.assertIn("Привіт", self.sent[-1].text)
        user = await self._user()
        self.assertEqual(user.language, "uk")
        self.assertFalse(user.onboarding_completed)
        # The rest of the flow runs in that language without any lang: tap.
        await self._send_text("Дівчина")
        self.assertIn("Скільки тобі років", self.sent[-1].text)
        await self._send_text("21")
        await self._send_text("Приймаю правила")
        self.assertTrue((await self._user()).onboarding_completed)
        self.assertIn("Головне меню", self.sent[-1].text)

    async def test_client_region_variant_is_normalized(self):
        # Telegram reports "ru-RU" / "uk-UA" for regional builds; the bot only
        # speaks the base codes.
        await self._send_text("/start", lang_code="ru-RU")
        self.assertNotIn("Choose your language", self.sent[-1].text)
        self.assertIn("Привет", self.sent[-1].text)
        self.assertEqual((await self._user()).language, "ru")

    async def test_unsupported_client_language_still_gets_the_picker(self):
        # German client, bot speaks ru/en/uk: the choice has to be offered,
        # and it has to work -- detection is a shortcut, not a wall.
        await self._send_text("/start", lang_code="de")
        self.assertIn("Выберите язык", self.sent[-1].text)
        self.assertIsNone((await self._user()).language)
        await self._tap("lang:en")
        self.assertIn("Hi!", self.sent[-1].text)
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

    async def test_every_unique_start_is_counted_once(self):
        from models.start import StartEvent
        await self._send_text("/start", update_id=901)
        self.assertEqual((await self._user()).start_count, 1)
        await self._send_text("/start", update_id=902)
        self.assertEqual((await self._user()).start_count, 2)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id))), 2)
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id)).where(
                StartEvent.event_type == "first_start")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
