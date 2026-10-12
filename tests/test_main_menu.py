"""The main menu: eight buttons, each one claimed by a handler, each screen
speaking the language its client asked in."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from handlers import load_routers
from keyboards import texts as T
from keyboards.reply import main_menu_kb
from utils.middleware import AccessMiddleware, AntiFloodMiddleware

MENU_BUTTONS = (T.SEARCH, T.PROFILE, T.SETTINGS, T.PREMIUM,
                T.STATS, T.HELP, T.CHAT, T.INVITE)
LANGS = ("ru", "en", "uk")


def message(text, lang_code="ru"):
    return Message(message_id=30, date=datetime.now(timezone.utc),
                   chat=Chat(id=123, type="private"),
                   from_user=User(id=123, is_bot=False, first_name="Test",
                                  language_code=lang_code),
                   text=text)


def callback(data, lang_code="ru"):
    return CallbackQuery(id="1", chat_instance="c", data=data,
                         from_user=User(id=123, is_bot=False, first_name="Test",
                                        language_code=lang_code),
                         message=message(""))


class _StubSession:
    """Stand-in for the SQLAlchemy session the invite screen opens on its own."""
    def __init__(self, user):
        self.user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, *_args, **_kwargs):
        return self.user


class MenuKeyboardTests(unittest.TestCase):
    def rows(self, lang):
        return [[b.text for b in row] for row in main_menu_kb(lang).keyboard]

    def test_the_keyboard_shows_exactly_the_eight_buttons(self):
        self.assertEqual(self.rows("ru"), [[T.SEARCH, T.PROFILE], [T.SETTINGS, T.PREMIUM],
                                           [T.STATS, T.HELP], [T.CHAT, T.INVITE]])

    def test_every_button_carries_an_icon(self):
        # Six of the eight did; the last two read as an unfinished row.
        for lang in LANGS:
            for row in main_menu_kb(lang).keyboard:
                for button in row:
                    with self.subTest(lang=lang, button=button.text):
                        self.assertTrue(button.icon_custom_emoji_id, button.text)

    def test_every_button_is_a_label_the_router_can_recognize(self):
        for lang in LANGS:
            labels = [b.text for row in main_menu_kb(lang).keyboard for b in row]
            self.assertEqual(len(labels), 8)
            self.assertEqual(len(set(labels)), 8)
            for label in labels:
                with self.subTest(lang=lang, label=label):
                    self.assertIn(label, T.SYSTEM_TEXTS)


class MenuDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for router in load_routers():
            router._parent_router = None
        self.dp = Dispatcher()
        self.dp.include_routers(*load_routers())
        self.dp.message.outer_middleware(AccessMiddleware())
        self.dp.callback_query.outer_middleware(AccessMiddleware())
        # Same two layers, same order, as bot.py: the guard has to be able to
        # see the language AccessMiddleware resolved.
        self.dp.message.middleware(AntiFloodMiddleware())
        self.dp.callback_query.middleware(AntiFloodMiddleware())
        self.bot = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")

    async def asyncTearDown(self):
        await self.dp.storage.close()
        for router in load_routers():
            router._parent_router = None

    async def press(self, key, lang, screen, row=None, lang_code=None,
                    partner=None, step="MENU", callback_data=None, flood=False):
        """Tap one menu button as a client whose interface language is `lang`,
        with the screen behind it replaced by a spy. `callback_data` sends the
        tap as an inline press instead of a reply-keyboard one, and `flood`
        makes the rate guard trip instead of letting the tap through."""
        telegram = AsyncMock(return_value=True)
        # The invite screen opens its own session instead of going through
        # UserRepository, so it needs a session that answers one query.
        invite_user = SimpleNamespace(language=lang, anon_id="#X", referral_code="ABCD",
                                      referrals_count=2, referrals_activated=1)
        if key is T.STATS:
            # Statistics has no screen function: get_user_stats *is* the entry.
            screen.return_value = {"chats_count": 1}
        with (
            patch.object(self.bot.session, "make_request", telegram),
            patch.object(Bot, "get_me", AsyncMock(return_value=User(
                id=1, is_bot=True, first_name="Bot", username="offline_test_bot"))),
            patch("models.base.async_session", lambda: _StubSession(invite_user)),
            patch("repositories.users.UserRepository.get", AsyncMock(return_value=row)),
            patch("repositories.users.UserRepository.get_or_create",
                  AsyncMock(return_value=(SimpleNamespace(language=lang), False))),
            patch("services.onboarding.banned", return_value=False),
            patch("services.onboarding.step_for", return_value=step),
            patch("services.chat_manager.set_online", AsyncMock()),
            patch("services.chat_manager.get_partner", AsyncMock(return_value=partner)),
            patch("utils.middleware.check_flood", AsyncMock(return_value=flood)),
            patch("services.growth.StreakService.checkin", AsyncMock()),
            patch("handlers.search.begin_search", screen),
            patch("handlers.profile.show_profile", screen),
            patch("handlers.settings.show_settings", screen),
            patch("handlers.premium.show_premium", screen),
            patch("handlers.stats.get_user_stats", screen if key is T.STATS
                  else AsyncMock(return_value={"chats_count": 1})),
        ):
            if callback_data is None:
                update = Update(update_id=30, message=message(T.label(key, lang),
                                                              lang_code or lang))
            else:
                update = Update(update_id=30, callback_query=callback(callback_data,
                                                                      lang_code or lang))
            await self.dp.feed_update(self.bot, update)
        return telegram

    async def test_every_button_opens_its_own_screen(self):
        screens = {key: AsyncMock() for key in MENU_BUTTONS}
        answered = {}
        for lang in LANGS:
            for key in MENU_BUTTONS:
                with self.subTest(lang=lang, key=key):
                    answered[(lang, key)] = await self.press(key, lang, screens[key])
        # Help, the community-chat row and the invite row have no dependency to
        # swap -- the handler is the screen -- so those are proven by the reply
        # they sent instead of by a spy.
        standalone = {T.HELP, T.CHAT, T.INVITE}
        for key, screen in screens.items():
            if key in standalone:
                continue
            self.assertTrue(screen.await_count, f"{key} never opened its screen")
        for key in standalone:
            for lang in LANGS:
                self.assertTrue(answered[(lang, key)].awaited,
                                f"{key} stayed silent in {lang}")

    async def test_a_menu_tap_mid_dialog_explains_itself_in_its_language(self):
        # Tapping a menu button from inside a live chat is refused before the
        # handler runs, and the refusal is a menu message too.
        for lang in LANGS:
            with self.subTest(lang=lang):
                telegram = await self.press(T.PROFILE, lang, AsyncMock(), partner=456)
                self.assertEqual(telegram.await_args.args[1].text,
                                 T.tr(T.GUARDS, lang)["in_chat"])

    async def test_a_menu_tap_before_registration_explains_itself_in_its_language(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                telegram = await self.press(T.PROFILE, lang, AsyncMock(),
                                            step="AGE", callback_data="profile")
                alert = next(m for m in (c.args[1] for c in telegram.await_args_list)
                             if type(m).__name__ == "AnswerCallbackQuery")
                self.assertEqual(alert.text, T.tr(T.GUARDS, lang)["register_first"])

    async def test_the_antiflood_notice_reaches_a_reply_keyboard(self):
        # It guarded on Message having a `.data`, which a Message never has, so
        # hammering the menu went quiet while inline taps still got a toast.
        for lang in LANGS:
            with self.subTest(lang=lang):
                telegram = await self.press(T.PROFILE, lang, AsyncMock(), flood=True)
                self.assertEqual(telegram.await_args.args[1].text,
                                 T.tr(T.GUARDS, lang)["slow_down"])

    async def test_help_follows_the_stored_language(self):
        from handlers.help import help_text
        for lang in LANGS:
            with self.subTest(lang=lang):
                telegram = await self.press(T.HELP, lang, AsyncMock(),
                                            row=SimpleNamespace(language=lang, anon_id="#X"))
                self.assertEqual(telegram.await_args.args[1].text, help_text(lang))

    async def test_help_follows_the_client_before_registration(self):
        # The help/rules path returns before the onboarding gate, so a reader who
        # has not registered yet has no stored language to fall back on -- the
        # client's own code is what has to decide, not a hardcoded Russian default.
        from handlers.help import help_text
        for lang_code in ("uk", "en"):
            with self.subTest(lang_code=lang_code):
                telegram = await self.press(T.HELP, "ru", AsyncMock(), row=None,
                                            lang_code=lang_code)
                self.assertEqual(telegram.await_args.args[1].text, help_text(lang_code))

    async def test_rules_follow_the_stored_language(self):
        from handlers.start import RULES_TEXT
        for lang in LANGS:
            with self.subTest(lang=lang):
                telegram = AsyncMock(return_value=True)
                with (
                    patch.object(self.bot.session, "make_request", telegram),
                    patch("repositories.users.UserRepository.get",
                          AsyncMock(return_value=SimpleNamespace(language=lang, anon_id="#X"))),
                    patch("repositories.users.UserRepository.get_or_create",
                          AsyncMock(return_value=(SimpleNamespace(language=lang), False))),
                    patch("services.onboarding.banned", return_value=False),
                    patch("services.onboarding.step_for", return_value="MENU"),
                    patch("services.chat_manager.set_online", AsyncMock()),
                    patch("services.chat_manager.get_partner", AsyncMock(return_value=None)),
                    patch("utils.middleware.check_flood", AsyncMock(return_value=False)),
                ):
                    await self.dp.feed_update(self.bot, Update(
                        update_id=31, message=message("/rules", "ru")))
                self.assertEqual(telegram.await_args.args[1].text, T.tr(RULES_TEXT, lang))


class ScreenLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_profile_card_follows_the_language(self):
        from handlers.profile import show_profile, P
        stats = {"gender": "male", "anon_id": "#A", "age": 25,
                 "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                 "profile_photo": None, "extra_photos_count": 0, "bio": None,
                 "chats_count": 3, "likes": 1, "dislikes": 0,
                 "is_premium": False, "premium_expires": datetime(2026, 2, 2, tzinfo=timezone.utc)}
        with patch("handlers.profile.get_user_stats", AsyncMock(return_value=stats)):
            for lang in LANGS:
                with self.subTest(lang=lang):
                    target = SimpleNamespace(answer=AsyncMock())
                    await show_profile(target, 1, lang)
                    text = target.answer.await_args.args[0]
                    self.assertIn(T.tr(P, lang)["card"].splitlines()[0], text)

    def test_the_profile_copy_is_actually_translated(self):
        from handlers.profile import P
        ru = T.tr(P, "ru")
        for lang in ("en", "uk"):
            words = T.tr(P, lang)
            for key in ("create", "edit_menu", "cancelled", "delete_yes", "back_profile", "share"):
                with self.subTest(lang=lang, key=key):
                    self.assertNotEqual(words[key], ru[key], f"{key} stayed Russian in {lang}")

    def test_the_back_buttons_follow_the_language(self):
        from keyboards.inline import settings_kb, stats_kb, help_kb, profile_edit_kb
        for lang in LANGS:
            with self.subTest(lang=lang):
                back = T.label(T.PAGE_PREV, lang)
                self.assertEqual([b.text for row in stats_kb(lang).inline_keyboard for b in row], [back])
                self.assertEqual([b.text for row in help_kb(lang).inline_keyboard for b in row], [back])
                self.assertEqual(profile_edit_kb(lang).inline_keyboard[-1][0].callback_data, "profile")
                if lang != "ru":
                    labels = [b.text for row in settings_kb(lang).inline_keyboard for b in row]
                    self.assertNotEqual(labels[0], "Мужчин")


if __name__ == "__main__":
    unittest.main(verbosity=2)
