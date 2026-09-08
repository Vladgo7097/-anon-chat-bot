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
            with (
                patch("services.profile.ProfileService.edit", AsyncMock(return_value=ServiceResult(False, "INVALID"))) as edit,
                patch.object(bot.session, "make_request", telegram),
            ):
                await state.set_state(ProfileEdit.AGE)
                await dp.feed_update(bot, Update(update_id=4, message=Message(
                    message_id=4, date=datetime.now(timezone.utc), chat=Chat(id=123, type="private"),
                    from_user=User(id=123, is_bot=False, first_name="Test"), text="²")))
                edit.assert_awaited_once_with(123, "age", None)
                await state.clear()
            from handlers.help import HELP_TEXT
            from handlers.stats import format_stats
            stats = {"chats_count": 7, "total_chat_seconds": 3660, "rating_pct": 80}
            with (
                patch("config.ADMIN_IDS", {99}),
                patch("handlers.stats.get_user_stats", AsyncMock(return_value=stats)) as get_stats,
                patch("services.admin.AdminService.find_user", AsyncMock(return_value=None)) as find_user,
                patch("handlers.admin.get_partner", AsyncMock(return_value=None)),
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
            await self.check_input_flows(dp, bot, telegram)
            await self.check_chat_controls(dp, bot, telegram)
            await self.check_payment_in_forms(dp, bot, telegram)
        finally:
            await bot.session.close()

    async def check_input_flows(self, dp, bot, telegram):
        from handlers.admin import AdminBroadcast, AdminMessage
        from handlers.profile import ProfileEdit
        from handlers.report import ReportFlow
        from handlers.start import Onboarding
        from handlers.help import HELP_TEXT
        from keyboards import texts as T

        async def dispatch(text):
            telegram.reset_mock()
            entities = ([MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=len(text))]
                        if text.startswith("/") else [])
            await dp.feed_update(bot, Update(update_id=20, message=Message(
                message_id=20, date=datetime.now(timezone.utc), chat=Chat(id=99, type="private"),
                from_user=User(id=99, is_bot=False, first_name="Admin"), text=text, entities=entities,
            )))

        state = dp.fsm.get_context(bot=bot, chat_id=99, user_id=99)
        with (
            patch("config.ADMIN_IDS", {99}),
            patch.object(bot.session, "make_request", telegram),
            patch("handlers.menu.set_online", AsyncMock()),
            patch("services.admin.AdminService.send_message_to_user", AsyncMock()) as send,
            patch("services.admin.AdminService.find_user", AsyncMock()) as find,
            patch("services.admin.AdminService.broadcast",
                  AsyncMock(return_value=ServiceResult(False, "INVALID"))) as broadcast,
            patch("handlers.stats.get_user_stats", AsyncMock(return_value={"chats_count": 1})),
        ):
            for flow in (ProfileEdit.PHOTO, ReportFlow.DETAILS, AdminMessage.CONTENT, AdminBroadcast.CONTENT):
                for action in (T.MENU, "/cancel"):
                    await state.set_state(flow)
                    await state.update_data(target_id=123, report_session="old")
                    await dispatch(action)
                    self.assertIsNone(await state.get_state())
                    self.assertEqual(await state.get_data(), {})
                    telegram.assert_awaited_once()
            for flow in (Onboarding.AGE, ProfileEdit.AGE, ReportFlow.DETAILS, AdminMessage.CONTENT):
                for action in (T.HELP, "/help"):
                    await state.set_state(flow)
                    await dispatch(action)
                    telegram.assert_awaited_once()
                    self.assertEqual(telegram.await_args.args[1].text, HELP_TEXT)
            for flow in (AdminMessage.CONTENT, AdminBroadcast.CONTENT):
                await state.set_state(flow)
                await state.update_data(target_id=123)
                await dispatch(T.STATS)
                self.assertIn("Твоя статистика", telegram.await_args.args[1].text)
            send.assert_not_awaited()
            broadcast.assert_not_awaited()
            for text in ("123", "#новости"):
                await state.set_state(AdminBroadcast.CONTENT)
                await state.update_data(audience="all")
                await dispatch(text)
                broadcast.assert_awaited_with(99, "all", text)
            find.assert_not_awaited()
            await state.clear()

    async def check_chat_controls(self, dp, bot, telegram):
        from types import SimpleNamespace
        from utils.middleware import AccessMiddleware
        from handlers.profile import ProfileEdit
        from handlers.report import ReportFlow
        from handlers.help import HELP_TEXT
        from keyboards import texts as T

        dp.message.outer_middleware(AccessMiddleware())
        state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)

        async def dispatch(text):
            telegram.reset_mock()
            entities = ([MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=len(text))]
                        if text.startswith("/") else [])
            await dp.feed_update(bot, Update(update_id=30, message=Message(
                message_id=30, date=datetime.now(timezone.utc), chat=Chat(id=123, type="private"),
                from_user=User(id=123, is_bot=False, first_name="Test"), text=text, entities=entities,
            )))

        with (
            patch.object(bot.session, "make_request", telegram),
            patch("repositories.users.UserRepository.get_or_create", AsyncMock(return_value=(SimpleNamespace(), False))),
            patch("services.onboarding.banned", return_value=False),
            patch("services.onboarding.step_for", return_value="MENU") as step,
            patch("services.chat_manager.set_online", AsyncMock()),
            patch("services.chat_manager.get_partner", AsyncMock(return_value=456)) as partner,
            patch("services.growth.StreakService.checkin", AsyncMock()),
            patch("handlers.search.begin_search", AsyncMock()) as search,
            patch("handlers.chat.finish", AsyncMock()) as finish,
            patch("handlers.chat.next_chat", AsyncMock()) as next_chat,
            patch("handlers.profile.show_profile", AsyncMock()) as profile,
            patch("handlers.settings.show_settings", AsyncMock()) as settings,
            patch("handlers.premium.show_premium", AsyncMock()) as premium,
            patch("handlers.stats.get_user_stats", AsyncMock(return_value={"chats_count": 1})) as stats,
        ):
            for action in (T.SEARCH, "/search"):
                await state.set_state(ProfileEdit.PHOTO)
                await state.update_data(age_page=2)
                await dispatch(action)
                search.assert_awaited()
                self.assertIsNone(await state.get_state())
                self.assertEqual(await state.get_data(), {})
            for action, called in ((T.STOP, finish), ("/stop", finish), (T.NEXT, next_chat)):
                await state.set_state(ReportFlow.DETAILS)
                await state.update_data(report_session="old")
                called.reset_mock()
                await dispatch(action)
                called.assert_awaited_once()
                self.assertIsNone(await state.get_state())
            await dispatch("/profile@offline_test_bot")
            profile.assert_not_awaited()
            self.assertIn("Ты сейчас в диалоге", telegram.await_args.args[1].text)
            partner.return_value = None
            for action, called in ((T.PROFILE, profile), (T.SETTINGS, settings),
                                   (T.PREMIUM, premium), (T.STATS, stats)):
                await state.set_state(ProfileEdit.PHOTO)
                called.reset_mock()
                await dispatch(action)
                called.assert_awaited_once()
                self.assertIsNone(await state.get_state())
            step.return_value = "AGE"
            await dispatch("/help@offline_test_bot")
            self.assertEqual(telegram.await_args.args[1].text, HELP_TEXT)

        with (
            patch.object(bot.session, "make_request", telegram),
            patch("config.ADMIN_IDS", {123}),
            patch("repositories.users.UserRepository.get_or_create", AsyncMock(return_value=(SimpleNamespace(), False))),
            patch("services.onboarding.banned", return_value=False),
            patch("services.onboarding.step_for", return_value="MENU"),
            patch("services.chat_manager.set_online", AsyncMock()),
            patch("services.growth.StreakService.checkin", AsyncMock()),
            patch("handlers.admin.get_partner", AsyncMock(return_value=456)),
            patch("services.admin.AdminService.find_user", AsyncMock()) as find,
            patch("services.relay.RelayService.prepare", AsyncMock(return_value=
                  ServiceResult(False, "NOT_CHATTING"))) as relay,
        ):
            for text in ("123", "#привет"):
                await dispatch(text)
                relay.assert_awaited_with(123, "text")
            find.assert_not_awaited()
            # A stale "Onboarding:*" FSM state left over from before the user
            # finished onboarding must not resurrect the onboarding handler for
            # an already-MENU user: aiogram's FSM middleware caches raw_state
            # once per update, so clearing storage without also clearing
            # data["raw_state"] leaves the router still matching the old state.
            relay.reset_mock()
            await state.set_state("Onboarding:GENDER")
            await dispatch("обычное сообщение собеседнику")
            relay.assert_awaited_once_with(123, "text")


    async def check_payment_in_forms(self, dp, bot, telegram):
        from aiogram.types import SuccessfulPayment
        from handlers.start import Onboarding
        from handlers.profile import ProfileEdit
        from handlers.report import ReportFlow
        from handlers.admin import AdminMessage, AdminBroadcast
        from utils.middleware import AntiFloodMiddleware

        dp.message.middleware(AntiFloodMiddleware())
        state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)
        with (
            patch.object(bot.session, "make_request", telegram),
            patch("utils.middleware.check_flood", AsyncMock(return_value=True)) as flood,
            patch("services.payments.PaymentService.complete", AsyncMock(return_value=
                  ServiceResult(True, "PAID", {"product": "premium_1m"}))) as complete,
        ):
            for flow in (None, Onboarding.AGE, ProfileEdit.PHOTO, ReportFlow.DETAILS,
                         AdminMessage.CONTENT, AdminBroadcast.CONTENT):
                await state.set_state(flow)
                complete.reset_mock()
                await dp.feed_update(bot, Update(update_id=40, message=Message(
                    message_id=40, date=datetime.now(timezone.utc), chat=Chat(id=123, type="private"),
                    from_user=User(id=123, is_bot=False, first_name="Test"),
                    successful_payment=SuccessfulPayment(currency="XTR", total_amount=100,
                        invoice_payload="offline", telegram_payment_charge_id="offline-charge",
                        provider_payment_charge_id=""),
                )))
                complete.assert_awaited_once_with(123, "offline", 100, "XTR", "offline-charge")
            flood.assert_not_awaited()
            await state.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
