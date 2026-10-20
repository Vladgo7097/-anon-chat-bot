"""ОП v2: уведомления владельцу о границах окон."""

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from test_op import BOT, OpCase

from models.operations import Notification
from services import op_schedule
from utils.time import utcnow


class BoundaryEntryTests(OpCase):
    def test_entries_catch_start_and_end(self):
        now = utcnow()
        settings = SimpleNamespace(
            starts_at=now - timedelta(minutes=2), ends_at=None
        )
        policy = SimpleNamespace(
            id=1, feature_key="*", starts_at=None, ends_at=now - timedelta(minutes=1)
        )
        target = SimpleNamespace(
            id=2, title="Канал", chat_id=-100,
            starts_at=now - timedelta(minutes=5), ends_at=None,
        )
        old_target = SimpleNamespace(
            id=3, title="Старый", chat_id=-200,
            starts_at=now - timedelta(minutes=30), ends_at=None,
        )
        entries = op_schedule._entries(settings, [policy], [target, old_target], now)
        found = {(kind, label) for kind, _, _, label in entries}
        self.assertEqual(
            found,
            {
                ("settings", "начало"),
                ("policy:1", "конец"),
                ("target:2", "начало"),
            },
        )
        # Граница тридцатиминутной давности вне окна наблюдения.
        self.assertNotIn(("target:3", "начало"), found)

    def test_text_names_scope(self):
        now = utcnow()
        text = op_schedule._text(BOT, "Общее окно ОП", now, "начало")
        self.assertIn("Общее окно ОП", text)
        self.assertIn("начало", text)


class ScheduleCheckTests(OpCase):
    async def test_boundary_notified_once(self):
        now = utcnow()
        await self.enable(starts_at=now - timedelta(minutes=2))
        with patch.object(op_schedule, "OWNER_ID", 999):
            await op_schedule.check(now=now)
            await op_schedule.check(now=now)
        async with self.factory() as s:
            rows = list(await s.scalars(select(Notification)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].type, "op_window")
        self.assertEqual(rows[0].user_id, 999)
        self.assertEqual(rows[0].bot_id, BOT)

    async def test_disabled_requirement_is_silent(self):
        now = utcnow()
        await self.enable(enabled=False, starts_at=now - timedelta(minutes=2))
        with patch.object(op_schedule, "OWNER_ID", 999):
            await op_schedule.check(now=now)
        async with self.factory() as s:
            rows = list(await s.scalars(select(Notification)))
        self.assertEqual(rows, [])

    async def test_paused_requirement_is_silent(self):
        now = utcnow()
        await self.enable(paused=True, starts_at=now - timedelta(minutes=2))
        with patch.object(op_schedule, "OWNER_ID", 999):
            await op_schedule.check(now=now)
        async with self.factory() as s:
            rows = list(await s.scalars(select(Notification)))
        self.assertEqual(rows, [])

    async def test_policy_and_target_windows(self):
        now = utcnow()
        await self.enable()
        target_id = await self.add_target(
            starts_at=now - timedelta(minutes=2)
        )
        await self.add_policy(
            starts_at=now - timedelta(minutes=2), groups=[("and", [target_id])]
        )
        with patch.object(op_schedule, "OWNER_ID", 999):
            await op_schedule.check(now=now)
        async with self.factory() as s:
            rows = list(await s.scalars(select(Notification)))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("ОП" in row.text for row in rows))

    async def test_no_owner_no_notifications(self):
        now = utcnow()
        await self.enable(starts_at=now - timedelta(minutes=2))
        with patch.object(op_schedule, "OWNER_ID", 0):
            self.assertEqual(await op_schedule.check(now=now), 0)
