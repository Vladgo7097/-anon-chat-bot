"""Admin overview additions: 7-day growth and the optional star-to-USD estimate."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_chat_duration import DatabaseCase
from models.payment import Payment
from models.user import User
from services.admin import AdminService, StatsService
from utils.time import utcnow


class GrowthTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The shared fixture users would otherwise count as "new this week".
        async with self.factory.begin() as s:
            await s.execute(User.__table__.delete())
        for target in ("config.OWNER_ID", "config.ADMIN_IDS"):
            p = patch(target, {99} if target == "config.ADMIN_IDS" else 99)
            p.start()
            self.addCleanup(p.stop)

    async def add_user(self, telegram_id, days_ago):
        async with self.factory.begin() as s:
            s.add(User(telegram_id=telegram_id, anon_id=f"#g{telegram_id}",
                       created_at=utcnow()-timedelta(days=days_ago)))

    async def test_growth_compares_last_two_seven_day_windows(self):
        for uid, days_ago in ((1, 1), (2, 3), (3, 5)):  # last 7 days: 3 users
            await self.add_user(uid, days_ago)
        for uid, days_ago in ((4, 9), (5, 12)):  # previous 7 days: 2 users
            await self.add_user(uid, days_ago)
        s = await StatsService().overview()
        self.assertEqual(s["new_7d"], 3)
        self.assertEqual(s["growth_7d_pct"], 50.0)

    async def test_growth_is_undefined_without_a_prior_week_to_compare(self):
        await self.add_user(1, 1)
        s = await StatsService().overview()
        self.assertEqual(s["new_7d"], 1)
        self.assertIsNone(s["growth_7d_pct"])

    async def test_growth_can_be_negative(self):
        await self.add_user(1, 1)
        for uid, days_ago in ((2, 9), (3, 10), (4, 11), (5, 12)):
            await self.add_user(uid, days_ago)
        s = await StatsService().overview()
        self.assertEqual(s["growth_7d_pct"], -75.0)


class StarsUsdTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for target, value in (("config.OWNER_ID", 99), ("config.ADMIN_IDS", {99})):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        async with self.factory.begin() as s:
            s.add(Payment(user_id=1, product="premium_1m", stars_amount=100,
                          status="paid", paid_at=utcnow()))

    async def test_no_dollar_estimate_without_a_configured_rate(self):
        s = await StatsService().overview()
        self.assertEqual(s["stars"], 100)
        self.assertIsNone(s["stars_usd"])

    async def test_dollar_estimate_uses_the_configured_rate(self):
        result = await AdminService().setting(99, "stars_usd_cents_per_100", 130)
        self.assertTrue(result.ok)
        s = await StatsService().overview()
        # 100 stars at 130 cents per 100 stars = $1.30
        self.assertEqual(s["stars_usd"], 1.30)

    async def test_rate_setting_rejects_out_of_range_values(self):
        result = await AdminService().setting(99, "stars_usd_cents_per_100", -1)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
