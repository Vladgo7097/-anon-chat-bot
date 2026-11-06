"""Red-team harness: admin surface — web token gate + Telegram admin panel.

Local only: SQLite factory + fastapi TestClient + a Bot whose session is an
AsyncMock. No network, no real Telegram, no touch of production files
outside tests/attack/.
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiogram.types import Poll, PollOption, Update  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

import config  # noqa: E402
from models.base import Base  # noqa: E402
from models.operations import Broadcast  # noqa: E402
from models.report import Report  # noqa: E402
from models.user import User  # noqa: E402

import models.chat  # noqa: F401,E402
import models.payment  # noqa: F401,E402
import models.ban  # noqa: F401,E402
from services.result import ServiceResult  # noqa: E402
from utils.time import utcnow  # noqa: E402

from test_attack_fsm import (  # noqa: E402
    AttackCase,
    BOT,
    _Payload,
    _Sent,
    next_uid,
    next_update_id,
)
from types import SimpleNamespace  # noqa: E402


class _SentWithMeta(_Sent):
    """_Sent + chat/message_id: cb_send читает их у карточки прогресса."""

    def __init__(self, bot, payload):
        super().__init__(bot, payload)
        self.chat = SimpleNamespace(id=self.chat_id)
        self.message_id = 1

    async def answer(self, text, **kwargs):
        return _SentWithMeta(self.bot, _Payload(text, self.chat_id))


TOKEN = "S3CRET-TOKEN-VALUE"
H = {"Authorization": f"Bearer {TOKEN}"}
OWNER = 777000111  # web-админ: _admin_id() всегда пишет аудит от его имени
FOREIGN_ADMIN = 999000  # второй админ, чужие черновики рассылок
TELEGRAM_ADMIN = 777111222  # админ в телеграм-панели

# Модули, которым подкладывается SQLite-фабрика (рецепт tests/test_chat_duration.py).
# services.chats в списке нет: там нет атрибута async_session.
WEB_PATCH_MODULES = (
    "models.base",
    "services.admin",
    "services.notifications",
    "services.settings",
    "repositories.users",
    "repositories.chats",
    "services.matching",
    "services.reports",
    "services.ads",
    "services.op",
    "services.reveal",
    "services.payments",
    "services.growth",
    "services.profile",
    "services.funnel_alert",
)


# --------------------------------------------------------------------------- #
# 1. Гейт web-админки: обход токена
# --------------------------------------------------------------------------- #
class TelegramSpoofTests(AttackCase):
    """Не-админ шлёт админ-команды: каждая должна упереться в AdminOnly
    (handlers/admin.py:41-52) — authorize() поднимает PermissionError,
    и обработчик не вызывается."""

    def _patch_admin_ids(self, ids):
        cm = patch.object(config, "ADMIN_IDS", set(ids))
        cm.start()
        self.addCleanup(cm.stop)

    def _record(self, *args, **kwargs):
        """Каркасный _Sent не несёт chat/message_id, а cb_send читает их у
        карточки прогресса ещё до вызова attach_progress."""
        payload = args[1]
        self.sent.append(payload)
        stub = _SentWithMeta(BOT, payload)
        return stub

    def _assert_denied(self):
        self.assertTrue(
            any("Нет доступа." in (text or "") for text in self.texts()),
            f"отказ AdminOnly не пришёл: {self.texts()!r}",
        )

    async def test_non_admin_callback_banf_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.admin.AdminService.resolve",
            AsyncMock(return_value=ServiceResult(True, "RESOLVED")),
        ) as resolve:
            await self.tap("admin:banf:1", uid)
        resolve.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_non_admin_callback_mute_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.admin.AdminService.resolve",
            AsyncMock(return_value=ServiceResult(True, "RESOLVED")),
        ) as resolve:
            await self.tap("admin:mute1:1", uid)
        resolve.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_non_admin_callback_send_job_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.admin.AdminService.confirm_broadcast", AsyncMock()
        ) as confirm:
            await self.tap("admin:send:1", uid)
        confirm.assert_not_awaited()
        self._assert_denied()

    async def test_non_admin_callback_grant_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch("services.admin.AdminService.grant", AsyncMock()) as grant:
            await self.tap("admin:dogrant:101:30", uid)
        grant.assert_not_awaited()
        self._assert_denied()

    async def test_non_admin_commands_are_denied(self):
        """/ban_user, /grant, /admin — и от зарегистрированного (AdminOnly),
        и от незарегистрированного (гейт онбординга раньше)."""
        registered = next_uid()
        raw = next_uid()
        await self.register(registered)
        with (
            patch("services.admin.AdminService.find_user", AsyncMock()) as find,
            patch("services.admin.AdminService.ban", AsyncMock()) as ban,
        ):
            await self.send("/ban_user 101 24 тест", raw)
            await self.send("/ban_user 101 24 тест", registered)
            await self.send("/grant 101 30 тест", registered)
            await self.send("/admin", registered)
        find.assert_not_awaited()
        ban.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_forced_admin_fsm_state_is_denied(self):
        """Состояние админа навязано напрямую в FSM-хранилище — AdminOnly
        всё равно перехватывает: middleware роутера стоит перед любым
        StateFilter-обработчиком."""
        uid = next_uid()
        await self.register(uid)
        await self.set_state(uid, "AdminMessage:CONTENT", target_id=101)
        with patch(
            "services.admin.AdminService.send_message_to_user", AsyncMock()
        ) as send:
            await self.send("принудительно записанное сообщение", uid)
        send.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_admin_send_job_control_passes(self):
        """Контроль: реальный админ подтверждает свою черновую рассылку
        полным прод-путём (AdminOnly -> confirm_broadcast -> карточка)."""
        self._patch_admin_ids({TELEGRAM_ADMIN})
        async with self.factory.begin() as s:
            job = Broadcast(
                admin_id=TELEGRAM_ADMIN,
                segment="all",
                content="контрольная рассылка",
                status="draft",
            )
            s.add(job)
            await s.flush()
            job_id = job.id
        # attach_progress глушим: каркасный _Sent не несёт message_id.
        # Сам confirm_broadcast идёт по-настоящему, через SQLite.
        with patch(
            "services.admin.AdminService.attach_progress",
            AsyncMock(return_value=ServiceResult(True, "ATTACHED")),
        ):
            await self.tap(f"admin:send:{job_id}", TELEGRAM_ADMIN)
        async with self.factory() as s:
            row = await s.get(Broadcast, job_id)
        self.assertEqual(row.status, "queued")
        self.assertTrue(
            any(f"Рассылка #{job_id}" in (text or "") for text in self.texts())
        )
        self.assertEqual(self.bot_errors, [])

    async def test_foreign_poll_update_is_harmless(self):
        """@admin_router.poll() (handlers/admin.py:686) не имеет AdminOnly
        по замыслу: любой получатель опроса шлёт свои голоса. Чужой poll_id —
        no-op в update_poll (services/notifications.py:163-169)."""
        await self.dp.feed_update(
            BOT,
            Update(
                update_id=next_update_id(),
                poll=Poll(
                    id="foreign-poll-id",
                    question="чужой опрос",
                    options=[PollOption(text="да", voter_count=7, persistent_id="p1")],
                    total_voter_count=7,
                    is_closed=True,
                    is_anonymous=True,
                    allows_revoting=False,
                    members_only=False,
                    type="regular",
                    allows_multiple_answers=False,
                ),
            ),
        )
        self.assertEqual(self.bot_errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
