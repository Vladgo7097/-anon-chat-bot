"""The funnel watcher: arrivals without completions must page the owner.

The language bug survived a week because nothing compared starts with
completions. These cases pin the two signatures, the quiet-day floors that
keep it from crying wolf, and the delivery path that actually reaches the
owner instead of being skipped by the outbox gate.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.operations import AnalyticsEvent, Notification
from services import funnel_alert
from services.notifications import NotificationService


class FunnelAlertTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for target, value in (("config.OWNER_ID", 99), ("config.ADMIN_IDS", {99})):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    async def seed(self, now, starts, finished):
        async with self.factory.begin() as s:
            for i in range(starts):
                s.add(AnalyticsEvent(user_id=i, event="start_source",
                                     dedupe_key=f"start_source:{i}:{now.date()}:{i}",
                                     created_at=now-timedelta(hours=2)))
            for i in range(finished):
                s.add(AnalyticsEvent(user_id=1000+i, event="onboarding_completed",
                                     dedupe_key=f"onboarding:{1000+i}",
                                     created_at=now-timedelta(hours=3)))

    async def pages(self):
        async with self.factory() as s:
            return (await s.scalars(select(Notification)
                                    .where(Notification.type == "funnel_alert"))).all()

    async def test_starts_without_completions_pages_once_per_day(self):
        now = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
        await self.seed(now, starts=25, finished=0)
        self.assertEqual(await funnel_alert.check(now), ("dead_flow", 25, 0))
        # A second pass ten minutes later must not queue a second page.
        await funnel_alert.check(now+timedelta(minutes=10))
        pages = await self.pages()
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].user_id, 99)
        self.assertIn(now.date().isoformat(), pages[0].dedupe_key)
        # And the outbox has to hand it over: an owner-only type that the
        # delivery gate skipped would be an alert nobody ever receives.
        sent = await NotificationService().next()
        self.assertIsNotNone(sent)
        self.assertEqual(sent.type, "funnel_alert")

    async def test_partial_break_still_pages(self):
        now = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
        # One language or one step dead: the rest keeps trickling through,
        # so a zero-completion rule alone would call this healthy.
        await self.seed(now, starts=120, finished=5)
        self.assertEqual((await funnel_alert.check(now))[0], "dead_flow")

    async def test_healthy_funnel_stays_silent(self):
        now = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
        await self.seed(now, starts=250, finished=180)
        self.assertIsNone(await funnel_alert.check(now))
        self.assertEqual(await self.pages(), [])

    async def test_quiet_day_is_not_a_bug(self):
        now = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
        await self.seed(now, starts=5, finished=0)
        self.assertIsNone(await funnel_alert.check(now))

    async def test_no_traffic_pages_in_working_hours_only(self):
        night, day = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc), \
            datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(await funnel_alert.check(night))
        self.assertEqual(await funnel_alert.check(day), ("no_traffic", 0, 0))
        self.assertEqual(len(await self.pages()), 1)

    async def test_without_owner_id_nothing_is_queued(self):
        now = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
        await self.seed(now, starts=25, finished=0)
        with patch("config.OWNER_ID", 0):
            self.assertIsNone(await funnel_alert.check(now))
        self.assertEqual(await self.pages(), [])


if __name__ == "__main__":
    unittest.main()
