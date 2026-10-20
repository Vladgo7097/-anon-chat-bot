"""ОП v2: ядро -- статусы getChatMember, правила AND/OR, сроки и шаблоны.

База -- SQLite-фабрика DatabaseCase; сеть не дёргается: вместо Telegram
работает FakeBot со словарём статусов. Гейт обязан закрываться на ошибке
API, открываться на исключении/владельце и складывать требования правил
в AND/OR-семантику из ТЗ.
"""

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select

from test_chat_duration import DatabaseCase

import models.op  # noqa: F401
from models.op import (
    OpErrorLog,
    OpExemption,
    OpMessageTemplate,
    OpPolicy,
    OpRuleGroup,
    OpRuleTarget,
    OpSettings,
    OpTarget,
)
from services import op as op_service
from utils.time import utcnow

BOT = 501
USER = 777


def member(status, is_member=None):
    stub = SimpleNamespace(status=status)
    if is_member is not None:
        stub.is_member = is_member
    return stub


class FakeBot:
    """Мини-бот: id для конфига, get_chat_member -- по словарю."""

    def __init__(self, bot_id=BOT):
        self.id = bot_id
        self.members = {}
        self.calls = []

    def allow(self, chat_id, user_id, status="member", is_member=None):
        self.members[(chat_id, user_id)] = member(status, is_member)

    def fail(self, chat_id, user_id, error=None):
        self.members[(chat_id, user_id)] = error or TelegramBadRequest(
            None, "Bad Request: chat not found"
        )

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        value = self.members.get((chat_id, user_id), member("left"))
        if isinstance(value, Exception):
            raise value
        return value


class OpCase(DatabaseCase):
    async def enable(self, **kwargs):
        kwargs.setdefault("enabled", True)
        kwargs.setdefault("timezone", "Europe/Kyiv")
        async with self.factory.begin() as s:
            settings = await s.get(OpSettings, BOT)
            if settings is None:
                settings = OpSettings(bot_id=BOT)
                s.add(settings)
            for key, value in kwargs.items():
                setattr(settings, key, value)
        return settings

    async def add_target(
        self, chat_id=-101, title="Канал А", link="https://t.me/a", **kw
    ):
        async with self.factory.begin() as s:
            target = OpTarget(
                bot_id=BOT, chat_id=chat_id, title=title, join_url=link, **kw
            )
            s.add(target)
        return target.id

    async def add_policy(self, feature_key="*", groups=(("and", ()),), **kw):
        async with self.factory.begin() as s:
            policy = OpPolicy(bot_id=BOT, feature_key=feature_key, **kw)
            s.add(policy)
            await s.flush()
            for mode, target_ids in groups:
                group = OpRuleGroup(policy_id=policy.id, mode=mode)
                s.add(group)
                await s.flush()
                for target_id in target_ids:
                    s.add(OpRuleTarget(group_id=group.id, target_id=target_id))
        return policy.id

    async def add_exemption(self, user_id=USER, until=None):
        async with self.factory.begin() as s:
            s.add(OpExemption(bot_id=BOT, user_id=user_id, until=until))

    def actor(self, user_id=USER, name="Тест", username=None):
        return SimpleNamespace(id=user_id, first_name=name, username=username)


class CoreGateTests(OpCase):
    async def test_disabled_requirement_allows(self):
        await self.enable(enabled=False)
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertFalse(gate.required)
        self.assertFalse(gate.blocked)

    async def test_paused_requirement_allows(self):
        await self.enable(paused=True)
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertFalse(gate.blocked)

    async def test_missing_target_blocks_and_offers_join(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertTrue(gate.required)
        self.assertTrue(gate.blocked)
        self.assertEqual(gate.missing_ids, {target_id})
        self.assertIn("Канал А", op_service.screen_text(gate, "ru"))
        markup = op_service.screen_kb(gate, "ru")
        urls = [
            button.url for row in markup.inline_keyboard for button in row if button.url
        ]
        datas = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("https://t.me/a", urls)
        self.assertEqual(datas, ["op:check"])

    async def test_membership_statuses(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        bot = FakeBot()
        for status in ("creator", "administrator", "member"):
            bot.allow(-101, USER, status)
            gate = await op_service.authorize(bot, self.actor())
            self.assertFalse(gate.blocked, status)
        bot.allow(-101, USER, "restricted", is_member=True)
        self.assertFalse((await op_service.authorize(bot, self.actor())).blocked)
        for status in ("restricted", "left", "kicked"):
            bot.allow(
                -101,
                USER,
                status,
                is_member=False if status == "restricted" else None,
            )
            gate = await op_service.authorize(bot, self.actor())
            self.assertTrue(gate.blocked, status)

    async def test_api_error_blocks_with_technical_message(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        bot = FakeBot()
        bot.fail(-101, USER)
        gate = await op_service.authorize(bot, self.actor())
        self.assertTrue(gate.blocked)
        self.assertIsNotNone(gate.error_detail)
        self.assertIn("Telegram не ответил", op_service.screen_text(gate, "ru"))
        markup = op_service.screen_kb(gate, "ru")
        self.assertEqual(
            len(markup.inline_keyboard), 1, "при ошибке -- только проверка"
        )
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(OpErrorLog.id))), 1)
            target = await s.get(OpTarget, target_id)
            self.assertEqual(target.setup_status, "error")
            self.assertIsNotNone(target.last_error)

    async def test_successful_check_marks_target_verified(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        bot = FakeBot()
        bot.allow(-101, USER, "member")
        await op_service.authorize(bot, self.actor())
        async with self.factory() as s:
            target = await s.get(OpTarget, target_id)
            self.assertEqual(target.setup_status, "verified")
            self.assertIsNone(target.last_error)

    async def test_exemption_allows(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        until = utcnow() + timedelta(days=1)
        await self.add_exemption(until=until)
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertFalse(gate.blocked)

    async def test_expired_exemption_does_not_allow(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        await self.add_exemption(until=utcnow() - timedelta(minutes=1))
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertTrue(gate.blocked)

    async def test_owner_bypasses_without_api_calls(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        bot = FakeBot()
        with patch.object(op_service.config, "ADMIN_IDS", {USER}):
            gate = await op_service.authorize(bot, self.actor())
        self.assertFalse(gate.blocked)
        self.assertEqual(bot.calls, [])


class RuleTests(OpCase):
    async def test_or_group_needs_any_target(self):
        await self.enable()
        first = await self.add_target(-101, "Канал А", "https://t.me/a")
        second = await self.add_target(-202, "Группа Б", "https://t.me/b")
        await self.add_policy(groups=[("or", [first, second])])
        bot = FakeBot()
        bot.allow(-101, USER, "member")
        gate = await op_service.authorize(bot, self.actor())
        self.assertFalse(gate.blocked, "одного ресурса из OR достаточно")

    async def test_or_group_missing_all_lists_both(self):
        await self.enable()
        first = await self.add_target(-101, "Канал А", "https://t.me/a")
        second = await self.add_target(-202, "Группа Б", "https://t.me/b")
        await self.add_policy(groups=[("or", [first, second])])
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertEqual(gate.missing_ids, {first, second})

    async def test_and_group_needs_every_target(self):
        await self.enable()
        first = await self.add_target(-101, "Канал А", "https://t.me/a")
        second = await self.add_target(-202, "Группа Б", "https://t.me/b")
        await self.add_policy(groups=[("and", [first, second])])
        bot = FakeBot()
        bot.allow(-101, USER, "member")
        gate = await op_service.authorize(bot, self.actor())
        self.assertTrue(gate.blocked)
        self.assertEqual(gate.missing_ids, {second})

    async def test_feature_policy_adds_to_global(self):
        await self.enable()
        main = await self.add_target(-101, "Канал А", "https://t.me/a")
        extra = await self.add_target(-202, "Группа Б", "https://t.me/b")
        await self.add_policy(groups=[("and", [main])])
        await self.add_policy(feature_key="premium", groups=[("and", [extra])])
        bot = FakeBot()
        bot.allow(-101, USER, "member")
        premium = await op_service.authorize(bot, self.actor(), "premium")
        self.assertTrue(premium.blocked)
        self.assertEqual(premium.missing_ids, {extra})
        other = await op_service.authorize(bot, self.actor(), "search")
        self.assertFalse(other.blocked, "правило premium не трогает поиск")

    async def test_expired_target_is_not_required(self):
        await self.enable()
        target_id = await self.add_target(ends_at=utcnow() - timedelta(minutes=1))
        await self.add_policy(groups=[("and", [target_id])])
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertFalse(gate.required)
        self.assertFalse(gate.blocked)

    async def test_future_policy_window_is_not_enforced(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(
            groups=[("and", [target_id])],
            starts_at=utcnow() + timedelta(hours=1),
        )
        gate = await op_service.authorize(FakeBot(), self.actor())
        self.assertFalse(gate.blocked)


class TemplateTests(OpCase):
    async def test_custom_template_renders_and_escapes(self):
        await self.enable(btn_join="Вступить", btn_check="Готово")
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        async with self.factory.begin() as s:
            s.add(
                OpMessageTemplate(
                    bot_id=BOT,
                    scope="global",
                    text="Привет, {first_name}! Не хватает: {missing_count}. Ты: {username}",
                    format="html",
                )
            )
        bot = FakeBot()
        gate = await op_service.authorize(
            bot, self.actor(name="<b>Боб</b>", username="bob<1>")
        )
        text = op_service.screen_text(gate, "ru")
        self.assertIn("Привет, &lt;b&gt;Боб&lt;/b&gt;!", text)
        self.assertIn("Не хватает: 1", text)
        self.assertIn("@bob&lt;1&gt;", text)
        labels = [
            button.text
            for row in op_service.screen_kb(gate, "ru").inline_keyboard
            for button in row
        ]
        self.assertIn("Вступить: Канал А", labels)
        self.assertIn("Готово", labels)

    async def test_feature_template_overrides_global(self):
        await self.enable()
        target_id = await self.add_target()
        await self.add_policy(groups=[("and", [target_id])])
        await self.add_policy(feature_key="premium", groups=[("and", [target_id])])
        async with self.factory.begin() as s:
            s.add(
                OpMessageTemplate(
                    bot_id=BOT, scope="global", text="GLOBAL", format="html"
                )
            )
            s.add(
                OpMessageTemplate(
                    bot_id=BOT,
                    scope="feature",
                    feature_key="premium",
                    text="FEATURE",
                    format="html",
                )
            )
        bot = FakeBot()
        premium = await op_service.authorize(bot, self.actor(), "premium")
        self.assertIn("FEATURE", op_service.screen_text(premium, "ru"))
        other = await op_service.authorize(bot, self.actor(), "search")
        self.assertIn("GLOBAL", op_service.screen_text(other, "ru"))


class HelperTests(unittest.TestCase):
    def test_gate_applies_and_feature_keys(self):
        self.assertFalse(op_service.gate_applies("/start", "/start"))
        self.assertFalse(op_service.gate_applies("/help", "/help"))
        self.assertFalse(op_service.gate_applies("", "op:check"))
        self.assertFalse(op_service.gate_applies("", "admin:op"))
        self.assertFalse(op_service.gate_applies("", "v1:buy:premium"))
        self.assertTrue(op_service.gate_applies("/search", "/search"))
        self.assertTrue(op_service.gate_applies("", "search:start"))
        self.assertEqual(op_service.feature_for("/search", "/search"), "search")
        self.assertEqual(op_service.feature_for("", "search:start"), "search")
        self.assertEqual(op_service.feature_for("", "Меню"), "*")

    def test_validate_and_render(self):
        self.assertIsNone(op_service.validate_template("Привет, {first_name}!"))
        self.assertIsNotNone(op_service.validate_template("Привет, {nope}!"))
        self.assertIsNotNone(op_service.validate_template("<b>не закрыт"))
        context = {"first_name": "<b>A</b>", "missing_count": 2}
        self.assertEqual(
            op_service.render("{first_name} {missing_count}", context, "html"),
            "&lt;b&gt;A&lt;/b&gt; 2",
        )
        self.assertEqual(
            op_service.render("{first_name}", {"first_name": "a_b"}, "markdown"),
            "a\\_b",
        )
