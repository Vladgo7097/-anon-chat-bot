"""Admin panel: section navigation, parameter catalog, audit trail, cancel flow."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.operations import Broadcast, Notification, AdminAudit
from models.user import User
from services.admin import (AdminService, SETTING_KEYS, SETTING_LIMITS, SETTING_TITLES,
                            SETTING_DEFAULTS, SETTING_PRESETS, SETTING_PAGE_SIZE)
from services.notifications import NotificationService, enqueue
from keyboards.inline import (admin_kb, admin_settings_kb, admin_setting_kb, admin_back_kb,
                              admin_growth_kb, admin_analytics_kb, admin_users_section_kb,
                              admin_moderation_kb, admin_broadcast_section_kb,
                              admin_settings_section_kb, admin_system_kb)
from utils.time import utcnow


class AdminPanelServicesTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.ADMIN_IDS", {99})
        p.start()
        self.addCleanup(p.stop)

    async def test_cancel_broadcast_drains_the_queue_and_writes_audit(self):
        admin = AdminService()
        job = (await admin.broadcast(99, "all", "hi")).data["job"]
        confirmed = await admin.confirm_broadcast(99, job.id)
        self.assertTrue(confirmed.ok)
        job = confirmed.data["job"]
        async with self.factory() as s:
            before = await s.scalar(select(func.count(Notification.id))
                                    .where(Notification.broadcast_id == job.id))
        async with self.factory.begin() as s:
            for uid in (1, 2):
                await enqueue(s, uid, "broadcast", f"broadcast:{job.id}:{uid}", "hi",
                              marketing=True, broadcast_id=job.id)
        result = await admin.cancel_broadcast(99, job.id)
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "CANCELLED")
        self.assertEqual(result.data["dropped"], before + 2)

        async with self.factory() as s:
            left = await s.scalar(select(func.count(Notification.id)).where(
                Notification.broadcast_id == job.id,
                Notification.status.in_(["queued", "sending"])))
            counts = await NotificationService().broadcast_counts(s, job.id)
            row = await s.get(Broadcast, job.id)
            entries = (await s.scalars(select(AdminAudit)
                                       .where(AdminAudit.action == "broadcast_cancel"))).all()
        self.assertEqual(left, 0)
        self.assertEqual(counts.get("skipped", 0), before + 2)
        self.assertEqual(row.status, "cancelled")
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].admin_id, 99)
        self.assertEqual(entries[0].target_id, str(job.id))

        again = await admin.cancel_broadcast(99, job.id)
        self.assertFalse(again.ok)
        self.assertEqual(again.code, "ALREADY_FINISHED")

        with self.assertRaises(PermissionError):
            await admin.cancel_broadcast(7, job.id)

    async def test_settings_all_covers_the_whole_catalog(self):
        values = await AdminService().settings_all(99)
        self.assertEqual(set(values), set(SETTING_KEYS))
        self.assertEqual(values["chat_delay_next"], SETTING_DEFAULTS["chat_delay_next"])

        service = AdminService()
        self.assertTrue((await service.setting(99, "chat_delay_next", 15)).ok)
        self.assertEqual((await AdminService().settings_all(99))["chat_delay_next"], 15)
        self.assertFalse((await service.setting(99, "chat_delay_next", 9999)).ok)
        self.assertEqual((await AdminService().settings_all(99))["chat_delay_next"], 15)

        with self.assertRaises(PermissionError):
            await AdminService().settings_all(7)

    async def test_audit_log_is_newest_first_and_pages(self):
        async with self.factory.begin() as s:
            for i in range(3):
                s.add(AdminAudit(admin_id=99, action=f"act{i}",
                                 target_type="user", target_id=str(i)))
        service = AdminService()
        first = await service.audit_log(99, 0, 2)
        self.assertEqual([r.action for r in first], ["act2", "act1"])
        second = await service.audit_log(99, 2, 2)
        self.assertEqual([r.action for r in second], ["act0"])
        with self.assertRaises(PermissionError):
            await service.audit_log(7, 0, 2)

    async def test_leaderboards_rank_spenders_and_newest_users(self):
        async with self.factory.begin() as s:
            s.add(User(telegram_id=11, anon_id="#poor", gender="male", age=25,
                       onboarding_completed=True, total_stars_spent=50,
                       created_at=utcnow()-timedelta(days=2)))
            s.add(User(telegram_id=12, anon_id="#whale", gender="female", age=25,
                       onboarding_completed=True, total_stars_spent=500,
                       created_at=utcnow()))
            s.add(User(telegram_id=13, anon_id="#gone", gender="male", age=25,
                       onboarding_completed=True, total_stars_spent=900,
                       created_at=utcnow(), deleted_at=utcnow()))
        service = AdminService()
        top = await service.top_spenders(99, 5)
        self.assertEqual([u.telegram_id for u in top], [12, 11])
        recent = await service.recent_users(99, 5)
        self.assertNotIn(13, [u.telegram_id for u in recent])
        self.assertEqual(recent[0].telegram_id, 12)
        with self.assertRaises(PermissionError):
            await service.top_spenders(7)

    async def test_system_status_reports_live_health(self):
        pool = AsyncMock()
        with patch("services.chat_manager.get_online_count", AsyncMock(return_value=7)), \
             patch("services.chat_manager.get_redis", AsyncMock(return_value=pool)):
            live = await AdminService().system_status(99)
        self.assertTrue(live["db"])
        self.assertTrue(live["redis"])
        pool.ping.assert_awaited()
        self.assertEqual(live["online"], 7)
        self.assertGreater(live["users"], 0)
        self.assertIsInstance(live["heartbeat_age"], (type(None), float))
        with self.assertRaises(PermissionError):
            await AdminService().system_status(7)


class CatalogConsistencyTests(unittest.TestCase):
    def test_every_key_has_a_title_a_default_and_safe_presets(self):
        for key in SETTING_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, SETTING_TITLES)
                self.assertIn(key, SETTING_DEFAULTS)
                self.assertIn(key, SETTING_PRESETS)
                low, high = SETTING_LIMITS[key]
                self.assertTrue(low <= SETTING_DEFAULTS[key] <= high)
                self.assertTrue(SETTING_PRESETS[key], "a setting without presets cannot be edited from the panel")
                for preset in SETTING_PRESETS[key]:
                    self.assertTrue(low <= preset <= high)

    def test_page_size_keeps_every_screen_under_ten_rows(self):
        self.assertLessEqual(SETTING_PAGE_SIZE, 8)
        self.assertEqual(SETTING_KEYS, tuple(SETTING_LIMITS))


class AdminPanelKeyboardsTests(unittest.TestCase):
    @staticmethod
    def callbacks(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    def test_root_offers_seven_sections_that_all_fit_the_payload_limit(self):
        data = self.callbacks(admin_kb())
        self.assertEqual(len(data), 7)
        self.assertEqual(len(set(data)), 7)
        for callback in data:
            self.assertTrue(callback.startswith("admin:sec:"), callback)
            self.assertLessEqual(len(callback.encode()), 64)

    def test_every_section_returns_to_the_root(self):
        for build in (admin_analytics_kb, admin_users_section_kb, admin_moderation_kb,
                      admin_broadcast_section_kb, admin_settings_section_kb, admin_system_kb):
            with self.subTest(section=build.__name__):
                self.assertEqual(build().inline_keyboard[-1][0].callback_data, "admin:back")

    def test_back_target_is_what_the_screen_asked_for(self):
        self.assertEqual(admin_back_kb().inline_keyboard[0][0].callback_data, "admin:back")
        self.assertEqual(admin_back_kb("admin:sec:users").inline_keyboard[0][0].callback_data,
                         "admin:sec:users")

    def test_settings_list_pages_both_directions(self):
        entries = [(key, SETTING_TITLES[key], 1) for key in SETTING_KEYS[:3]]
        first = self.callbacks(admin_settings_kb(entries, 1, 2))
        self.assertIn("admin:setlist:2", first)
        self.assertNotIn("admin:setlist:0", first)
        last = self.callbacks(admin_settings_kb(entries, 2, 2))
        self.assertIn("admin:setlist:1", last)
        self.assertNotIn("admin:setlist:3", last)
        for callback in first + last:
            self.assertLessEqual(len(callback.encode()), 64)

    def test_setting_editor_marks_the_live_value_and_offers_an_escape_hatch(self):
        kb = admin_setting_kb("chat_delay_next", 2, SETTING_PRESETS["chat_delay_next"], 7)
        data = self.callbacks(kb)
        self.assertIn("admin:set:chat_delay_next:7", data)
        self.assertIn("admin:setc:chat_delay_next", data)
        self.assertIn("admin:setlist:2", data)
        marked = [b.text for row in kb.inline_keyboard for b in row if b.text.startswith("✓")]
        self.assertEqual(marked, ["✓ 7"])

    def test_growth_kb_marks_the_active_period(self):
        data = self.callbacks(admin_growth_kb(7))
        self.assertEqual(len(data), 4)
        self.assertIn("admin:sec:analytics", data)
        labels = [b.text for row in admin_growth_kb(7).inline_keyboard for b in row]
        self.assertIn("7 дней ✓", labels)


class BroadcastCancelRenderTests(unittest.TestCase):
    def job(self, status):
        return SimpleNamespace(id=5, segment="all", status=status, total=10, blocked=0,
                               sent=3, failed=0, started_at=None, finished_at=None, kind="text")

    def test_card_names_the_cancelled_status(self):
        from handlers.admin import broadcast_card
        text = broadcast_card(self.job("cancelled"), {"sent": 3, "skipped": 7})
        self.assertIn("остановлена вручную", text)
        self.assertIn("Пропущено", text)

    def test_stop_button_only_appears_while_the_wave_is_moving(self):
        from handlers.admin import broadcast_card_kb
        for status in ("queued", "sending"):
            with self.subTest(status=status):
                self.assertIn("admin:bcancel:5", self.callbacks(broadcast_card_kb(5, status)))
        for status in ("completed", "cancelled", "draft"):
            with self.subTest(status=status):
                self.assertNotIn("admin:bcancel:5", self.callbacks(broadcast_card_kb(5, status)))

    @staticmethod
    def callbacks(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]


if __name__ == "__main__":
    unittest.main(verbosity=2)
