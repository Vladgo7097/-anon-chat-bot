"""ОП: обязательная подписка -- гейт, сроки ресурсов, рассылка и команды.

База -- SQLite-фабрика DatabaseCase; сеть не дёргается: вместо Telegram
работает FakeBot со словарём статусов. Отдельно проверяются: гейт по
апдейту, перепроверка в том же сообщении, срок ресурса, автоотключение
воркером, рассылка неподписанным и owner-команды /setup, /unsetup, /op.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChatMember
from sqlalchemy import select
from test_chat_duration import DatabaseCase

import models.op  # noqa: F401
from models.op import OpChat, OpSettings
from models.operations import AdminAudit, Notification
from models.user import User
from services import op as op_service
from services.notifications import NotificationService, enqueue
from utils.time import utcnow

BOT = 501
ADMIN = 99
LINK_A = "https://t.me/kanal_a"
LINK_B = "https://t.me/gruppa_b"


def member(status, is_member=None):
    stub = SimpleNamespace(status=status)
    if is_member is not None:
        stub.is_member = is_member
    return stub


def channel_stub(chat_id, title, username=None):
    fields = {
        "id": chat_id,
        "title": title,
        "type": SimpleNamespace(value="channel"),
    }
    if username:
        fields["username"] = username
    return SimpleNamespace(**fields)


def owner_message(text, bot):
    return SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        bot=bot,
        from_user=SimpleNamespace(id=ADMIN, username="owner", language_code="ru"),
        text=text,
        answer=AsyncMock(),
    )


class FakeBot:
    """Мини-бот: id для конфига, get_chat_member/get_chat -- по словарю."""

    def __init__(self, bot_id=BOT):
        self.id = bot_id
        self.members = {}
        self.chats = {}
        self.calls = []

    def allow(self, chat_id, user_id, status="member", is_member=None):
        self.members[(chat_id, user_id)] = member(status, is_member)

    def fail(self, chat_id, user_id, error):
        self.members[(chat_id, user_id)] = error

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        value = self.members.get((chat_id, user_id), member("left"))
        if isinstance(value, Exception):
            raise value
        return value

    async def get_chat(self, target):
        chat = self.chats.get(target)
        if chat is None:
            raise RuntimeError("chat not found")
        return chat

    async def create_chat_invite_link(self, chat_id, name=None):
        raise RuntimeError("invite links unavailable")


class OpCase(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for target in ("services.op._cache_get", "services.op._cache_set"):
            p = patch(target, AsyncMock(return_value=None))
            p.start()
            self.addCleanup(p.stop)

    async def op_on(
        self,
        *,
        bot_id=BOT,
        enabled=True,
        starts_at=None,
        ends_at=None,
        text=None,
        chats=(("Канал А", -101, LINK_A), ("Группа Б", -202, LINK_B)),
    ):
        async with self.factory.begin() as s:
            s.add(
                OpSettings(
                    bot_id=bot_id,
                    enabled=enabled,
                    starts_at=starts_at if starts_at is not None else utcnow(),
                    ends_at=ends_at,
                    text=text,
                )
            )
            for entry in chats:
                title, chat_id, link = entry[:3]
                chat_ends = entry[3] if len(entry) > 3 else None
                s.add(
                    OpChat(
                        bot_id=bot_id,
                        chat_id=chat_id,
                        title=title,
                        kind="channel",
                        link=link,
                        active=True,
                        rights_ok=True,
                        ends_at=chat_ends,
                    )
                )

    async def add_user(self, telegram_id, *, bot_id=BOT, lang="ru"):
        async with self.factory.begin() as s:
            s.add(
                User(
                    telegram_id=telegram_id,
                    anon_id=f"#op{telegram_id}",
                    bot_id=bot_id,
                    language=lang,
                )
            )

    async def settings(self, bot_id=BOT):
        async with self.factory() as s:
            return await s.scalar(select(OpSettings).where(OpSettings.bot_id == bot_id))

    async def audits(self, actions):
        async with self.factory() as s:
            return list(
                (
                    await s.scalars(
                        select(AdminAudit.action)
                        .where(AdminAudit.action.in_(actions))
                        .order_by(AdminAudit.id)
                    )
                ).all()
            )

    async def expire_chat(self, chat_id, *, minutes=1):
        async with self.factory.begin() as s:
            row = await s.scalar(select(OpChat).where(OpChat.chat_id == chat_id))
            row.ends_at = utcnow() - timedelta(minutes=minutes)


class OpGateServiceTests(OpCase):
    async def test_op_off_lets_everyone_through(self):
        bot = FakeBot()
        gate = await op_service.op_gate(bot, 7001)
        self.assertFalse(gate.required)
        self.assertFalse(gate.blocked)
        self.assertEqual(bot.calls, [])

    async def test_unsubscribed_sees_every_resource(self):
        await self.op_on()
        bot = FakeBot()
        gate = await op_service.op_gate(bot, 7001)
        self.assertTrue(gate.required)
        self.assertTrue(gate.blocked)
        self.assertEqual([c["title"] for c in gate.missing], ["Канал А", "Группа Б"])
        text = op_service.op_screen_text(gate, "ru")
        self.assertIn("Канал А", text)
        self.assertIn("Группа Б", text)
        kb = op_service.op_screen_kb(gate, "ru")
        self.assertEqual(
            [b.url for row in kb.inline_keyboard[:-1] for b in row], [LINK_A, LINK_B]
        )
        self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "op:check")
        settings = await self.settings()
        self.assertEqual(settings.checks_total, 1)
        self.assertEqual(settings.checks_blocked, 1)
        self.assertEqual(settings.checks_error, 0)

    async def test_partial_subscription_lists_only_the_missing_one(self):
        await self.op_on()
        bot = FakeBot()
        bot.allow(-101, 7002)
        gate = await op_service.op_gate(bot, 7002)
        self.assertTrue(gate.blocked)
        self.assertEqual([c["title"] for c in gate.missing], ["Группа Б"])
        text = op_service.op_screen_text(gate, "ru")
        self.assertIn("Группа Б", text)
        self.assertNotIn("Канал А", text)
        self.assertEqual(len(op_service.op_screen_kb(gate, "ru").inline_keyboard), 2)

    async def test_a_user_who_unsubscribes_is_blocked_again(self):
        await self.op_on()
        bot = FakeBot()
        bot.allow(-101, 7003)
        bot.allow(-202, 7003)
        self.assertTrue((await op_service.op_gate(bot, 7003)).subscribed)
        bot.allow(-202, 7003, "left")
        self.assertTrue((await op_service.op_gate(bot, 7003)).blocked)

    async def test_every_subscribed_status_counts(self):
        await self.op_on(
            chats=(
                ("К1", -101, LINK_A),
                ("К2", -102, LINK_A),
                ("К3", -103, LINK_A),
                ("К4", -104, LINK_A),
            )
        )
        bot = FakeBot()
        bot.allow(-101, 7004, "creator")
        bot.allow(-102, 7004, "administrator")
        bot.allow(-103, 7004, "member")
        bot.allow(-104, 7004, "restricted", is_member=True)
        gate = await op_service.op_gate(bot, 7004)
        self.assertTrue(gate.subscribed)
        self.assertFalse(gate.blocked)

    async def test_restricted_without_is_member_does_not_count(self):
        await self.op_on(chats=(("К1", -101, LINK_A),))
        bot = FakeBot()
        bot.allow(-101, 7005, "restricted", is_member=False)
        gate = await op_service.op_gate(bot, 7005)
        self.assertTrue(gate.blocked)
        self.assertEqual([c["title"] for c in gate.missing], ["К1"])
        bot.allow(-101, 7005, "kicked")
        self.assertTrue((await op_service.op_gate(bot, 7005)).blocked)

    async def test_api_errors_never_open_access(self):
        await self.op_on()
        bot = FakeBot()
        bot.fail(-101, 7006, RuntimeError("timeout"))
        bot.fail(
            -202,
            7006,
            TelegramBadRequest(
                method=GetChatMember(chat_id=-202, user_id=7006),
                message="chat not found",
            ),
        )
        gate = await op_service.op_gate(bot, 7006)
        self.assertTrue(gate.required)
        self.assertTrue(gate.blocked)
        self.assertEqual(gate.missing, [])
        self.assertEqual(gate.errors, ["Канал А", "Группа Б"])
        text = op_service.op_screen_text(gate, "ru")
        self.assertIn(op_service.tr(op_service.ERROR_TEXT, "ru"), text)
        settings = await self.settings()
        self.assertEqual(settings.checks_error, 1)
        self.assertEqual(settings.checks_blocked, 1)

    async def test_manual_off_lifts_the_gate_immediately(self):
        await self.op_on(enabled=False)
        bot = FakeBot()
        gate = await op_service.op_gate(bot, 7007)
        self.assertFalse(gate.required)
        self.assertFalse(gate.blocked)
        self.assertEqual(bot.calls, [])

    async def test_expired_resource_lifts_the_gate_and_tick_greens_it_out(self):
        await self.op_on(chats=(("Канал А", -101, LINK_A),))
        await self.expire_chat(-101)
        bot = FakeBot()
        self.assertFalse((await op_service.op_gate(bot, 7008)).required)
        self.assertEqual(bot.calls, [])
        self.assertEqual(await op_service.tick(), 2)
        settings = await self.settings()
        self.assertFalse(settings.enabled)
        row = await op_service.chat_row(BOT, -101)
        self.assertFalse(row.active)
        self.assertEqual(
            await self.audits(["op_chat_expired", "op_auto_disable"]),
            ["op_chat_expired", "op_auto_disable"],
        )
        self.assertEqual(await op_service.tick(), 0)

    async def test_tick_keeps_the_requirement_while_a_second_resource_lives(self):
        await self.op_on()
        await self.expire_chat(-101)
        self.assertEqual(await op_service.tick(), 1)
        self.assertTrue((await self.settings()).enabled)
        bot = FakeBot()
        gate = await op_service.op_gate(bot, 7010)
        self.assertTrue(gate.required)
        self.assertEqual([c["title"] for c in gate.missing], ["Группа Б"])

    async def test_positive_cache_short_circuits_the_second_check(self):
        await self.op_on()
        bot = FakeBot()
        bot.allow(-101, 7009)
        bot.allow(-202, 7009)
        store = {}

        async def cache_get(key):
            return store.get(key)

        async def cache_set(key):
            store[key] = "1"

        with (
            patch("services.op._cache_get", cache_get),
            patch("services.op._cache_set", cache_set),
        ):
            first = await op_service.op_gate(bot, 7009)
            calls = len(bot.calls)
            second = await op_service.op_gate(bot, 7009)
        self.assertTrue(first.subscribed)
        self.assertTrue(second.subscribed)
        self.assertEqual(len(bot.calls), calls)
        settings = await self.settings()
        self.assertEqual(settings.checks_total, 1)

    async def test_limit_parser_understands_seconds_to_days(self):
        self.assertEqual(op_service.parse_limit("30s"), timedelta(seconds=30))
        self.assertEqual(op_service.parse_limit("15m"), timedelta(minutes=15))
        self.assertEqual(op_service.parse_limit("2h"), timedelta(hours=2))
        self.assertEqual(op_service.parse_limit("1d"), timedelta(days=1))
        for bad in ("1w", "0d", "d", "", "10", "1 d"):
            with self.assertRaises(ValueError, msg=bad):
                op_service.parse_limit(bad)

    async def test_setup_chat_activates_only_with_admin_rights(self):
        bot = FakeBot()
        bot.chats["@kanal"] = channel_stub(-301, "Канал", "kanal")
        bot.allow(-301, BOT, "member")
        ok, note = await op_service.setup_chat(bot, BOT, "@kanal", ADMIN)
        self.assertFalse(ok)
        self.assertIn("не администратор", note)
        self.assertIsNone(await op_service.chat_row(BOT, -301))
        self.assertIsNone(await self.settings())
        bot.allow(-301, BOT, "administrator")
        ok, note = await op_service.setup_chat(bot, BOT, "@kanal", ADMIN)
        self.assertTrue(ok)
        self.assertIn("без ограничений", note)
        row = await op_service.chat_row(BOT, -301)
        self.assertTrue(row.active)
        self.assertTrue(row.rights_ok)
        self.assertEqual(row.link, "https://t.me/kanal")
        settings = await self.settings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.campaign_id, 1)

    async def test_parse_chat_ref_understands_links_and_ids(self):
        self.assertEqual(op_service.parse_chat_ref("@kanal"), "@kanal")
        self.assertEqual(op_service.parse_chat_ref("t.me/kanal"), "@kanal")
        self.assertEqual(op_service.parse_chat_ref("https://t.me/kanal/"), "@kanal")
        self.assertEqual(op_service.parse_chat_ref("-1001234567890"), -1001234567890)
        self.assertIsNone(op_service.parse_chat_ref("https://t.me/+AbCdEf"))
        self.assertIsNone(op_service.parse_chat_ref("joinchat/AbCdEf"))
        self.assertIsNone(op_service.parse_chat_ref(""))


class OpNotifyTests(OpCase):
    async def test_sweep_notices_only_unsubscribed_and_never_duplicates(self):
        await self.op_on()
        await self.add_user(1001)
        await self.add_user(1002, lang="uk")
        bot = FakeBot()
        bot.allow(-101, 1001)
        bot.allow(-202, 1001)
        bot.allow(-101, 1002)  # не подписан только на «Группу Б»
        with patch("utils.bots.resolve", lambda bot_id: bot if bot_id == BOT else None):
            self.assertEqual(await op_service.request_notify(BOT, ADMIN), 2)
            self.assertTrue(await op_service.notify_tick())
            async with self.factory() as s:
                rows = list(
                    (
                        await s.scalars(
                            select(Notification).where(Notification.type == "op_notice")
                        )
                    ).all()
                )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].user_id, 1002)
            self.assertEqual(rows[0].bot_id, BOT)
            self.assertIn("Группа Б", rows[0].text)
            self.assertTrue(rows[0].dedupe_key.endswith(":1002:0"))
            self.assertEqual((await self.settings()).notify_sent, 1)
            # повторный запуск в той же кампании дублей не плодит
            await op_service.request_notify(BOT, ADMIN)
            await op_service.notify_tick()
            await op_service.notify_tick()
            async with self.factory() as s:
                again = list(
                    (
                        await s.scalars(
                            select(Notification).where(Notification.type == "op_notice")
                        )
                    ).all()
                )
        self.assertEqual(len(again), 1)
        settings = await self.settings()
        self.assertFalse(settings.notify_running)
        async with self.factory() as s:
            done = await s.scalar(
                select(AdminAudit).where(AdminAudit.action == "op_notify_done")
            )
        self.assertIsNotNone(done)

    async def test_queued_op_notice_is_skipped_when_op_is_off(self):
        await self.op_on(enabled=False)
        await self.add_user(1005)
        key = f"op_notice:{BOT}:1005:0"
        async with self.factory.begin() as s:
            await enqueue(s, 1005, "op_notice", key, "бла", bot_id=BOT)
        self.assertIsNone(await NotificationService().next())
        async with self.factory() as s:
            row = await s.scalar(
                select(Notification).where(Notification.dedupe_key == key)
            )
        self.assertEqual(row.status, "skipped")


class OpOwnerCommandsTests(OpCase):
    async def test_setup_command_starts_op_with_limit_and_audit(self):
        from handlers.op import cmd_setup

        bot = FakeBot()
        bot.chats["@kanal"] = channel_stub(-301, "Канал", "kanal")
        bot.allow(-301, BOT, "administrator")
        message = owner_message("/setup @kanal 2h", bot)
        await cmd_setup(message)
        settings = await self.settings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.campaign_id, 1)
        row = await op_service.chat_row(BOT, -301)
        self.assertTrue(row.active)
        self.assertTrue(row.rights_ok)
        self.assertEqual(row.link, "https://t.me/kanal")
        left = row.ends_at - utcnow()
        self.assertGreater(left, timedelta(hours=1, minutes=55))
        self.assertLess(left, timedelta(hours=2, minutes=5))
        self.assertIn("ОП запущена", message.answer.await_args.args[0])
        self.assertEqual(
            await self.audits(["op_enable", "op_chat_add"]),
            ["op_enable", "op_chat_add"],
        )

    async def test_setup_command_takes_an_explicit_invite_link(self):
        from handlers.op import cmd_setup

        bot = FakeBot()
        bot.chats[-301] = channel_stub(-301, "Канал")
        bot.allow(-301, BOT, "administrator")
        message = owner_message("/setup -301 https://t.me/+secret 30m", bot)
        await cmd_setup(message)
        row = await op_service.chat_row(BOT, -301)
        self.assertEqual(row.link, "https://t.me/+secret")
        left = row.ends_at - utcnow()
        self.assertLess(left, timedelta(minutes=31))

    async def test_setup_without_rights_is_refused_and_changes_nothing(self):
        from handlers.op import cmd_setup

        bot = FakeBot()
        bot.chats["@kanal"] = channel_stub(-301, "Канал", "kanal")
        bot.allow(-301, BOT, "member")
        message = owner_message("/setup @kanal", bot)
        await cmd_setup(message)
        self.assertIn("не администратор", message.answer.await_args.args[0])
        self.assertIsNone(await op_service.chat_row(BOT, -301))
        self.assertIsNone(await self.settings())

    async def test_setup_rejects_a_bad_limit(self):
        from handlers.op import cmd_setup

        bot = FakeBot()
        bot.chats["@kanal"] = channel_stub(-301, "Канал", "kanal")
        bot.allow(-301, BOT, "administrator")
        message = owner_message("/setup @kanal 5w", bot)
        await cmd_setup(message)
        self.assertIn("Не понял лимит", message.answer.await_args.args[0])
        self.assertIsNone(await self.settings())

    async def test_setup_without_args_shows_the_help(self):
        from handlers.op import cmd_setup

        message = owner_message("/setup", FakeBot())
        await cmd_setup(message)
        text = message.answer.await_args.args[0]
        self.assertIn("Запуск ОП", text)
        self.assertIn("/unsetup", text)

    async def test_unsetup_one_then_all(self):
        from handlers.op import cmd_unsetup

        await self.op_on()
        message = owner_message("/unsetup -101", FakeBot())
        await cmd_unsetup(message)
        async with self.factory() as s:
            row = await s.scalar(select(OpChat).where(OpChat.chat_id == -101))
        self.assertFalse(row.active)
        self.assertIsNone(row.ends_at)
        self.assertTrue((await self.settings()).enabled)
        self.assertIn("ОП отключена", message.answer.await_args.args[0])
        message = owner_message("/unsetup", FakeBot())
        await cmd_unsetup(message)
        self.assertFalse((await self.settings()).enabled)
        async with self.factory() as s:
            rows = list((await s.scalars(select(OpChat))).all())
        self.assertTrue(all(not r.active for r in rows))
        self.assertIn("Все ОП отключены", message.answer.await_args.args[0])

    async def test_unsetup_of_the_last_live_resource_disables_the_requirement(self):
        from handlers.op import cmd_unsetup

        await self.op_on(chats=(("Канал А", -101, LINK_A),))
        message = owner_message("/unsetup -101", FakeBot())
        await cmd_unsetup(message)
        self.assertFalse((await self.settings()).enabled)
        self.assertEqual(await self.audits(["op_disable"]), ["op_disable"])

    async def test_op_menu_lists_live_chats_with_controls(self):
        from handlers.op import cmd_op

        await self.op_on(chats=(("Канал А", -101, LINK_A),))
        message = owner_message("/op", FakeBot())
        await cmd_op(message)
        text = message.answer.await_args.args[0]
        markup = message.answer.await_args.kwargs["reply_markup"]
        self.assertIn("активные ОП: 1", text)
        self.assertIn("Канал А", text)
        self.assertIn("-101", text)
        data = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("op_owner:off:-101", data)
        self.assertIn("op_owner:off_all", data)
        self.assertIn("op_owner:notify", data)

    async def test_op_menu_with_nothing_active_keeps_only_refresh(self):
        from handlers.op import cmd_op

        message = owner_message("/op", FakeBot())
        await cmd_op(message)
        self.assertIn("Активных ОП нет", message.answer.await_args.args[0])
        markup = message.answer.await_args.kwargs["reply_markup"]
        data = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertEqual(data, ["op_owner:list"])

    async def test_row_button_off_disables_and_rerenders(self):
        from handlers.op import cb_owner_off

        await self.op_on(chats=(("Канал А", -101, LINK_A),))
        callback = SimpleNamespace(
            bot=SimpleNamespace(id=BOT),
            from_user=SimpleNamespace(id=ADMIN),
            data="op_owner:off:-101",
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )
        await cb_owner_off(callback)
        self.assertFalse((await op_service.chat_row(BOT, -101)).active)
        self.assertFalse((await self.settings()).enabled)
        shown = callback.message.edit_text.await_args.args[0]
        self.assertIn("Активных ОП нет", shown)

    async def test_notify_button_refuses_when_nothing_is_active(self):
        from handlers.op import cb_owner_notify

        callback = SimpleNamespace(
            bot=SimpleNamespace(id=BOT),
            from_user=SimpleNamespace(id=ADMIN),
            answer=AsyncMock(),
        )
        await cb_owner_notify(callback)
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert"))
        self.assertIsNone(await self.settings())

    async def test_forwarded_channel_message_gives_id_only_to_the_owner(self):
        from handlers.op import forwarded_id

        origin = SimpleNamespace(chat=channel_stub(-100500, "Канал", "kanal"))
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=SimpleNamespace(id=ADMIN),
            forward_origin=origin,
            answer=AsyncMock(),
        )
        with patch("handlers.op.authorize"):
            await forwarded_id(message)
        text = message.answer.await_args.args[0]
        self.assertIn("-100500", text)
        self.assertIn("/setup @kanal", text)
        stranger = SimpleNamespace(
            chat=SimpleNamespace(type="private"),
            from_user=SimpleNamespace(id=12345),
            forward_origin=origin,
            answer=AsyncMock(),
        )
        with patch("handlers.op.authorize", side_effect=PermissionError):
            with self.assertRaises(SkipHandler):
                await forwarded_id(stranger)
        stranger.answer.assert_not_awaited()

    async def test_admin_gate_blocks_strangers_and_lets_the_owner_in(self):
        from handlers.admin import AdminOnly

        middleware = AdminOnly()
        handler = AsyncMock()
        stranger = SimpleNamespace(
            from_user=SimpleNamespace(id=12345), answer=AsyncMock()
        )
        with patch("handlers.admin.authorize", side_effect=PermissionError):
            await middleware(handler, stranger, {})
        handler.assert_not_awaited()
        stranger.answer.assert_awaited_once_with("⛔ Нет доступа.")
        owner = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN), answer=AsyncMock())
        with patch("handlers.admin.authorize"):
            await middleware(handler, owner, {})
        handler.assert_awaited_once()

    async def test_check_button_edits_the_same_message_and_opens_the_bot(self):
        from handlers.op import cb_op_check

        await self.op_on()
        bot = FakeBot()
        bot.allow(-101, 8001)  # на «Группу Б» не подписан
        callback = SimpleNamespace(
            bot=bot,
            from_user=SimpleNamespace(
                id=8001, username=None, first_name="T", language_code="ru"
            ),
            message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        entered = AsyncMock()
        with patch("handlers.start.enter_bot", entered):
            await cb_op_check(callback, state)
            callback.message.edit_text.assert_awaited()
            text = callback.message.edit_text.await_args.args[0]
            markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
            self.assertIn("Группа Б", text)
            self.assertEqual(markup.inline_keyboard[-1][0].callback_data, "op:check")
            callback.message.answer.assert_not_awaited()
            entered.assert_not_awaited()
            bot.allow(-202, 8001)
            await cb_op_check(callback, state)
        entered.assert_awaited_once()
        self.assertIn(
            op_service.tr(op_service.OK_TEXT, "ru"),
            callback.message.edit_text.await_args.args[0],
        )


class OpMiddlewareTests(OpCase):
    async def test_middleware_stops_a_blocked_user_with_the_screen(self):
        from aiogram import Bot, Dispatcher
        from aiogram.types import Chat, Message, Update, User as TgUser

        from handlers import load_routers
        from utils.middleware import AccessMiddleware

        bot = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")
        bot._me = TgUser(
            id=1, is_bot=True, first_name="Test", username="offline_op_bot"
        )
        await self.op_on(bot_id=bot.id)
        fake = FakeBot(bot_id=bot.id)
        fake.allow(-101, 7100, "left")
        fake.allow(-202, 7100, "left")
        bot.get_chat_member = fake.get_chat_member
        telegram = AsyncMock(return_value=True)
        routers = load_routers()
        for router in routers:
            router._parent_router = None
        dp = Dispatcher()
        dp.include_routers(*routers)
        dp.message.outer_middleware(AccessMiddleware())
        try:
            with patch.object(bot.session, "make_request", telegram):
                await dp.feed_update(
                    bot,
                    Update(
                        update_id=1,
                        message=Message(
                            message_id=1,
                            date=datetime.now(timezone.utc),
                            chat=Chat(id=7100, type="private"),
                            from_user=TgUser(
                                id=7100,
                                is_bot=False,
                                first_name="Blocked",
                                language_code="ru",
                            ),
                            text="привет",
                        ),
                    ),
                )
        finally:
            await dp.storage.close()
            for router in routers:
                router._parent_router = None
        sent = [
            call.args[1]
            for call in telegram.await_args_list
            if getattr(call.args[1], "__api_method__", None) == "sendMessage"
        ]
        self.assertEqual(len(sent), 1)
        self.assertIn("Канал А", sent[0].text)
        self.assertIn("Группа Б", sent[0].text)
        self.assertEqual(
            sent[0].reply_markup.inline_keyboard[-1][0].callback_data, "op:check"
        )
