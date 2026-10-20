"""ОП v2: панель -- обзор требования, ресурсы, права бота и аудит."""

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from test_op import BOT, USER, OpCase as _OpCase

import models.op  # noqa: F401
from models.op import OpAuditLog, OpErrorLog
from services import op_admin as panel
from utils.time import utcnow


class PanelBot:
    """Бот для панели: права в чате и выдача инвайт-ссылки."""

    def __init__(self, state="administrator", fail=False):
        self.id = BOT
        self.bot_state = state
        self.fail = fail
        self.invites = 0

    async def get_chat_member(self, chat_id, user_id):
        if self.fail:
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(None, "Bad Request: chat not found")
        return SimpleNamespace(status=self.bot_state)

    async def create_chat_invite_link(self, chat_id, name=None):
        self.invites += 1
        return SimpleNamespace(invite_link="https://t.me/+invite")


def chat(chat_id=-1001, title="Канал А", kind="channel", username=None):
    return SimpleNamespace(id=chat_id, title=title, type=kind, username=username)


class PanelTests(_OpCase):
    async def test_overview_requires_live_target_and_policy(self):
        data = await panel.overview(BOT)
        self.assertFalse(data["required"])
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        data = await panel.overview(BOT)
        self.assertTrue(data["required"])
        self.assertEqual(len(data["targets"]), 1)

    async def test_expired_target_drops_requirement(self):
        await self.enable()
        target_id = await self.add_target(ends_at=utcnow() - timedelta(minutes=1))
        await self.add_policy(groups=[("and", [target_id])])
        data = await panel.overview(BOT)
        self.assertFalse(data["required"])

    async def test_add_public_target_builds_link_and_audits(self):
        target, error = await panel.add_target(
            PanelBot(), BOT, USER, chat(username="chan")
        )
        self.assertIsNone(error)
        self.assertEqual(target.join_url, "https://t.me/chan")
        self.assertEqual(target.setup_status, "verified")
        async with self.factory() as s:
            actions = list(await s.scalars(select(OpAuditLog.action)))
        self.assertIn("target_add", actions)

    async def test_private_target_gets_invite_link(self):
        bot = PanelBot()
        target, error = await panel.add_target(
            bot, BOT, USER, chat(chat_id=-1002, title="Закрытая", kind="supergroup")
        )
        self.assertIsNone(error)
        self.assertEqual(target.join_url, "https://t.me/+invite")
        self.assertEqual(bot.invites, 1)

    async def test_target_without_admin_rights_is_error(self):
        bot = PanelBot(state="member")
        target, error = await panel.add_target(
            bot, BOT, USER, chat(chat_id=-1003, title="Группа", kind="supergroup")
        )
        self.assertIsNotNone(error)
        self.assertTrue(error.startswith("not_admin"))
        self.assertEqual(target.setup_status, "error")
        self.assertEqual(bot.invites, 0)

    async def test_failed_rights_check_is_error(self):
        target, error = await panel.add_target(
            PanelBot(fail=True), BOT, USER, chat(chat_id=-1004)
        )
        self.assertIsNotNone(error)
        self.assertEqual(target.setup_status, "error")

    async def test_toggle_and_delete_write_audit(self):
        target, _ = await panel.add_target(PanelBot(), BOT, USER, chat(username="chan"))
        updated = await panel.set_target_active(BOT, USER, target.id, False)
        self.assertFalse(updated.is_active)
        self.assertTrue(await panel.delete_target(BOT, USER, target.id))
        self.assertIsNone(await panel.get_target(BOT, target.id))
        async with self.factory() as s:
            actions = list(await s.scalars(select(OpAuditLog.action)))
        self.assertIn("target_active:0", actions)
        self.assertIn("target_delete", actions)

    async def test_join_url_validation(self):
        target, _ = await panel.add_target(PanelBot(), BOT, USER, chat(username="chan"))
        with self.assertRaises(ValueError):
            await panel.set_join_url(BOT, USER, target.id, "https://example.com/join")
        updated = await panel.set_join_url(BOT, USER, target.id, "https://t.me/other")
        self.assertEqual(updated.join_url, "https://t.me/other")

    async def test_check_all_counts(self):
        bot = PanelBot()
        await panel.add_target(bot, BOT, USER, chat(chat_id=-1005, username="a"))
        await panel.add_target(bot, BOT, USER, chat(chat_id=-1006, username="b"))
        bot.bot_state = "member"
        result = await panel.check_all(bot, BOT)
        self.assertEqual(result, {"ok": 0, "error": 2})

    async def test_diagnostics_lists_errors(self):
        await self.enable()
        async with self.factory.begin() as s:
            s.add(OpErrorLog(bot_id=BOT, kind="check", detail="forbidden"))
        data = await panel.diagnostics(BOT)
        self.assertGreaterEqual(data["errors_24h"], 1)
        self.assertEqual(data["errors"][0].detail, "forbidden")

    async def test_audit_page_orders_newest_first(self):
        async with self.factory.begin() as s:
            s.add(OpAuditLog(bot_id=BOT, action="old"))
            s.add(OpAuditLog(bot_id=BOT, action="new"))
        rows = await panel.audit_page(BOT)
        self.assertEqual([row.action for row in rows], ["new", "old"])


class MessageTemplateTests(_OpCase):
    async def test_save_and_read_back(self):
        await panel.save_template(
            BOT, USER, "global", None, "Привет, {first_name}!", "html"
        )
        row = await panel.get_template(BOT, "global", None)
        self.assertEqual(row.text, "Привет, {first_name}!")
        self.assertEqual(row.format, "html")
        async with self.factory() as s:
            actions = list(await s.scalars(select(OpAuditLog.action)))
        self.assertIn("template_save", actions)

    async def test_unknown_variable_rejected(self):
        with self.assertRaises(ValueError):
            await panel.save_template(BOT, USER, "global", None, "{nope}", "html")
        self.assertIsNone(await panel.get_template(BOT, "global", None))

    async def test_broken_html_rejected(self):
        with self.assertRaises(ValueError):
            await panel.save_template(BOT, USER, "error", None, "<b>не закрыт", "html")
        self.assertIsNone(await panel.get_template(BOT, "error", None))

    async def test_feature_template_is_separate(self):
        await panel.save_template(BOT, USER, "global", None, "G", "plain")
        await panel.save_template(BOT, USER, "feature", "premium", "P", "plain")
        self.assertEqual((await panel.get_template(BOT, "global", None)).text, "G")
        self.assertEqual(
            (await panel.get_template(BOT, "feature", "premium")).text, "P"
        )

    async def test_format_requires_existing_template(self):
        self.assertIsNone(
            await panel.set_template_format(BOT, USER, "global", None, "markdown")
        )

    async def test_delete_template(self):
        await panel.save_template(BOT, USER, "global", None, "X", "plain")
        self.assertTrue(await panel.delete_template(BOT, USER, "global", None))
        self.assertIsNone(await panel.get_template(BOT, "global", None))

    async def test_button_labels_set_and_reset(self):
        settings = await panel.set_button_label(BOT, USER, "join", "Вступить")
        self.assertEqual(settings.btn_join, "Вступить")
        settings = await panel.set_button_label(BOT, USER, "join", None)
        self.assertIsNone(settings.btn_join)

    async def test_button_label_length(self):
        with self.assertRaises(ValueError):
            await panel.set_button_label(BOT, USER, "check", "x" * 65)

    async def test_feature_keys_include_builtin_and_policy(self):
        await self.add_policy(feature_key="premium", groups=[("and", [])])
        keys = await panel.feature_keys(BOT)
        self.assertIn("premium", keys)
        self.assertIn("search", keys)
        self.assertNotIn("*", keys)
