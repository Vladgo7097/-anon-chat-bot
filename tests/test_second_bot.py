"""The second bot: same code, same database, Ukrainian by default.

dj's spec for the extra token -- same interface and functions, one shared
database with the original, and Ukrainian as its default language -- turns on
three things that are easy to get wrong when the two bots share one process:
which bot a message leaves through, what language a row that has not picked
one yet renders in, and whether opening the second bot forks the profile.
"""
import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types.user import User as TgUser
from sqlalchemy import select

from handlers import load_routers
from handlers.start import TEXTS
from keyboards import texts as T
from models.base import Base
from models.user import User as UserModel
from repositories.users import UserRepository
from utils import bots
from utils.middleware import AccessMiddleware, AntiFloodMiddleware
import models.chat  # noqa: F401 -- registers metadata used by the routers
import models.operations  # noqa: F401
import models.payment  # noqa: F401
import models.report  # noqa: F401

from test_chat_duration import DatabaseCase


def user(language_code=None):
    return TgUser(id=1, is_bot=False, first_name="T", language_code=language_code)


class RegistryMixin:
    """Two fake bots registered before every test, cleared afterwards."""

    def setUp(self):
        bots._bot_by_id.clear()
        bots._primary = None
        self.addCleanup(bots._bot_by_id.clear)
        self.addCleanup(setattr, bots, "_primary", None)
        self.first = SimpleNamespace(id=111, default_lang="ru", detect_client_lang=True)
        self.second = SimpleNamespace(id=222, default_lang="uk", detect_client_lang=False)
        bots.register(self.first, primary=True)
        bots.register(self.second)


class FallbackLanguageTests(RegistryMixin, unittest.TestCase):
    def test_the_primary_bot_follows_the_clients_own_language(self):
        self.assertEqual(bots.default_lang(self.first, user("uk")), "uk")
        self.assertEqual(bots.default_lang(self.first, user("de")), "ru")
        self.assertEqual(bots.default_lang(self.first, user(None)), "ru")

    def test_the_second_bot_is_ukrainian_whatever_its_client_runs_in(self):
        # A Russian client is exactly who this bot must not hand Russian to:
        # it exists to reach the Ukrainian audience, and Settings still has
        # the picker for anyone who wants otherwise.
        for code in ("ru", "en", "uk", "de", None):
            self.assertEqual(bots.default_lang(self.second, user(code)), "uk")

    def test_without_an_actor_only_the_bots_own_default_decides(self):
        self.assertEqual(bots.default_lang(self.first), "ru")
        self.assertEqual(bots.default_lang(self.second), "uk")

    def test_bot_id_comes_straight_out_of_the_token(self):
        # No getMe round-trip: the id is the numeric prefix, so registering
        # both bots at startup costs nothing.
        self.assertEqual(Bot(token="7828301604:SYNTHETIC").id, 7828301604)
        self.assertEqual(Bot(token="111:AAA").id, 111)


class BotRoutingTests(RegistryMixin, DatabaseCase):
    async def test_a_row_without_a_bot_still_reaches_the_primary(self):
        self.assertIs(await bots.bot_for(1), self.first)
        self.assertIs(await bots.bot_for(1, self.second), self.second)

    async def test_a_notice_follows_the_bot_the_row_names(self):
        async with self.factory.begin() as s:
            row = await s.scalar(select(UserModel).where(UserModel.telegram_id == 1))
            row.bot_id = self.second.id
        self.assertIs(await bots.bot_for(1), self.second)
        # ...and the caller's own bot is not allowed to override that, or a
        # bot2 user would be served by the token they never opened.
        self.assertIs(await bots.bot_for(1, self.first), self.second)

    async def test_get_or_create_records_the_delivering_bot(self):
        created_row, is_new = await UserRepository().get_or_create(501, "u", "N", bot_id=222)
        self.assertTrue(is_new)
        self.assertEqual(created_row.bot_id, 222)

    async def test_a_user_who_switches_bots_is_rerouted_not_stale(self):
        await UserRepository().get_or_create(502, "u", "N", bot_id=111)
        again, is_new = await UserRepository().get_or_create(502, "u", "N", bot_id=222)
        # The 60-second write-throttle fast path must not hand back the row
        # still pointing at the first bot.
        self.assertFalse(is_new)
        self.assertEqual(again.bot_id, 222)
        third, _ = await UserRepository().get_or_create(502, "u", "N", bot_id=222)
        self.assertEqual(third.bot_id, 222)

    async def test_the_same_row_serves_both_bots(self):
        """Opening the second bot must not fork the profile -- one database,
        one anonymous id, one pool that both bots draw from."""
        first_row, _ = await UserRepository().get_or_create(503, "u", "N", bot_id=111)
        second_row, is_new = await UserRepository().get_or_create(503, "u", "N", bot_id=222)
        self.assertFalse(is_new)
        self.assertEqual(second_row.id, first_row.id)
        self.assertEqual(second_row.anon_id, first_row.anon_id)


class WorkerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_notice_leaves_through_the_recipients_own_bot(self):
        from workers import maintenance
        notification = SimpleNamespace(id=1, user_id=123, type="match", text="hi",
                                       callback_data=None, attempt_count=1, broadcast_id=None)
        service = SimpleNamespace(schedule=AsyncMock(), expand_broadcast=AsyncMock(),
                                  progress_updates=AsyncMock(return_value=[]),
                                  drop_progress=AsyncMock(),
                                  next=AsyncMock(side_effect=[notification, asyncio.CancelledError()]),
                                  finish=AsyncMock(), broadcast_poll=AsyncMock(return_value=None))
        fallback = SimpleNamespace(send_message=AsyncMock(), default_lang="ru")
        theirs = SimpleNamespace(send_message=AsyncMock(), default_lang="uk")
        with (
            patch.object(maintenance, "NotificationService", return_value=service),
            patch.object(maintenance.Path, "write_text"),
            patch("workers.maintenance.ChatService.reconcile", AsyncMock()),
            patch("workers.maintenance.asyncio.sleep", AsyncMock()),
            patch.object(maintenance.funnel_alert, "check", AsyncMock(return_value=None)),
            patch.object(maintenance.bots, "bot_for", AsyncMock(return_value=theirs)),
            patch("utils.db.user_lang", AsyncMock(return_value="uk")) as language,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await maintenance.run(fallback)
        theirs.send_message.assert_awaited_once()
        fallback.send_message.assert_not_awaited()
        # The routed bot also decides the fallback language, so a recipient
        # who never picked one is not defaulted back to Russian.
        language.assert_awaited_once_with(123, "uk")


class CrossBotRelayTests(unittest.IsolatedAsyncioTestCase):
    """Пара на разных ботах должна доставляться ботом получателя.

    copy_message копирует внутри одного бота, и до правки каждое сообщение
    между пользователем первого бота и пользователем второго падало в 400 --
    162 обрыва за шесть часов на одних только кросс-бот парах.
    """

    async def test_a_message_leaves_through_the_partners_own_bot(self):
        from handlers import chat as chat_handlers
        mine, theirs = SimpleNamespace(id=111), SimpleNamespace(id=222)
        message = SimpleNamespace(
            text="привет", caption=None, content_type="text",
            from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=1),
            message_id=10, bot=mine, answer=AsyncMock(), reply_to_message=None)
        active = SimpleNamespace(user1_id=1, user2_id=2, session_id="s1")
        with (
            patch("handlers.chat.ChatService.active", AsyncMock(return_value=active)),
            patch("services.relay.RelayService.contact_warning_required",
                  AsyncMock(return_value=False)),
            patch("services.relay.RelayService.remember", AsyncMock()),
            patch("services.relay.RelayService.delivered", AsyncMock()),
            patch("services.relay.RelayService.remember_context", AsyncMock()),
            patch.object(chat_handlers.bots, "bot_for",
                         AsyncMock(return_value=theirs)) as route,
            patch.object(chat_handlers.repost, "copy",
                         AsyncMock(return_value=SimpleNamespace(message_id=77))) as copy,
        ):
            await chat_handlers.forward_message(message)
        route.assert_awaited_once_with(2, mine)
        # Бот получателя идёт первым аргументом: именно он доставляет.
        self.assertIs(copy.await_args.args[0], theirs)
        # Источник -- сам Message, а не его идентификаторы: Bot API не умеет
        # достать сообщение по id, и прежняя возня с get_messages рвала в
        # AttributeError каждое кросс-бот сообщение.
        self.assertIs(copy.await_args.kwargs["source"], message)

    async def test_edits_and_deletes_also_go_through_the_delivering_bot(self):
        from handlers import chat as chat_handlers
        mine, theirs = SimpleNamespace(id=111), SimpleNamespace(id=222)
        active = SimpleNamespace(user1_id=1, user2_id=2, session_id="s1")
        edited = SimpleNamespace(
            text="правка", caption=None, content_type="text",
            from_user=SimpleNamespace(id=1), message_id=10, bot=mine,
            answer=AsyncMock())
        reply = SimpleNamespace(message_id=10)
        deleting = SimpleNamespace(
            text="/delete", caption=None, content_type="text",
            from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=1),
            message_id=11, bot=mine, answer=AsyncMock(), reply_to_message=reply)
        with (
            patch("handlers.chat.ChatService.active", AsyncMock(return_value=active)),
            patch("services.relay.RelayService.contact_warning_required",
                  AsyncMock(return_value=False)),
            patch("services.relay.RelayService.reply_target",
                  AsyncMock(return_value=55)),
            patch("services.relay.RelayService.remember_context", AsyncMock()),
            patch.object(chat_handlers.bots, "bot_for",
                         AsyncMock(return_value=theirs)) as route,
        ):
            theirs.edit_message_text = AsyncMock()
            await chat_handlers.sync_edited_message(edited)
            theirs.edit_message_text.assert_awaited_once_with(
                "правка", chat_id=2, message_id=55, parse_mode=None)
            route.assert_awaited_once_with(2, mine)

            route.reset_mock()
            theirs.delete_message = AsyncMock()
            await chat_handlers.delete_relayed_message(deleting)
            theirs.delete_message.assert_awaited_once_with(2, 55)
            route.assert_awaited_once_with(2, mine)


# The real token lives in .env and never in the repo: dispatch below only needs
# a second Bot whose id differs from the primary's, and nothing here talks to
# Telegram. Hardcoding a secret is how the first one ended up rotting in git.
SECOND = Bot(token="7828301604:OFFLINE_TEST_TOKEN",
             default=DefaultBotProperties(parse_mode=ParseMode.HTML))
SECOND.default_lang = "uk"
SECOND.detect_client_lang = False
PRIMARY = Bot(token="111:TEST_TOKEN_FOR_OFFLINE_DISPATCH",
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
PRIMARY.default_lang = "ru"
PRIMARY.detect_client_lang = True

_next_uid = [9500]


class SecondBotOnboardingTests(unittest.IsolatedAsyncioTestCase):
    """End to end through the production middleware chain."""

    async def asyncSetUp(self):
        import tempfile
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        self.uid = _next_uid[0]
        _next_uid[0] += 1
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "second.db"))
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in ("models.base", "repositories.chats", "repositories.users",
                       "services.notifications", "services.growth", "services.payments",
                       "services.profile", "services.matching", "services.settings",
                       "services.reveal", "services.reports", "services.admin", "services.ads"):
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
        for instance in (SECOND, PRIMARY):
            request = AsyncMock(side_effect=self._record)
            p = patch.object(instance.session, "make_request", request)
            p.start()
            self.addCleanup(p.stop)
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
        payload = args[1]
        if getattr(payload, "__api_method__", None) == "sendMessage":
            self.sent.append(payload)
        return True

    async def _start(self, instance, language_code, update_id=1):
        await self.dp.feed_update(instance, Update(update_id=update_id, message=Message(
            message_id=update_id, date=datetime.now(timezone.utc),
            chat=Chat(id=self.uid, type="private"),
            from_user=TgUser(id=self.uid, is_bot=False, first_name="T",
                             language_code=language_code), text="/start")))

    async def _tap(self, instance, data, update_id=1):
        await self.dp.feed_update(instance, Update(update_id=update_id, callback_query=CallbackQuery(
            id=str(update_id), from_user=TgUser(id=self.uid, is_bot=False, first_name="T"),
            chat_instance="offline", data=data, message=Message(
                message_id=update_id, date=datetime.now(timezone.utc),
                chat=Chat(id=self.uid, type="private"),
                from_user=TgUser(id=1, is_bot=True, first_name="Test", username="t"), text="stub"))))

    async def _user(self):
        async with self.factory() as s:
            return await s.scalar(select(UserModel).where(UserModel.telegram_id == self.uid))

    async def test_the_second_bot_opens_in_ukrainian_for_a_russian_client(self):
        await self._start(SECOND, "ru")
        row = await self._user()
        self.assertEqual(row.language, "uk")
        self.assertEqual(self.sent[0].text, T.tr(TEXTS["WELCOME"], "uk"))

    async def test_the_primary_bot_still_follows_the_client(self):
        await self._start(PRIMARY, "ru", update_id=2)
        row = await self._user()
        self.assertEqual(row.language, "ru")
        self.assertEqual(self.sent[0].text, T.tr(TEXTS["WELCOME"], "ru"))

    async def test_the_second_bot_opens_in_ukrainian_for_an_english_client(self):
        # The default is not "whatever this client speaks" -- an en client on
        # the Ukrainian bot still lands on Ukrainian until they pick.
        await self._start(SECOND, "en", update_id=3)
        row = await self._user()
        self.assertEqual(row.language, "uk")
        self.assertEqual(self.sent[0].text, T.tr(TEXTS["WELCOME"], "uk"))

    async def test_an_explicit_choice_outranks_the_bots_default(self):
        # The picker in Settings: uk arrived as the second bot's default, and
        # switching to ru has to stick across later updates.
        await self._start(SECOND, "ru", update_id=4)
        row = await self._user()
        self.assertEqual(row.language, "uk")
        await self._tap(SECOND, "lang:ru", update_id=5)
        self.assertEqual((await self._user()).language, "ru")
        await self._start(SECOND, "ru", update_id=6)
        self.assertEqual((await self._user()).language, "ru")


if __name__ == "__main__":
    unittest.main(verbosity=2)
