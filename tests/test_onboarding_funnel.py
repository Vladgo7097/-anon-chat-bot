"""The registration funnel: where signups stop, and the age-gate refusals."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from handlers.admin import funnel_text
from models.user import User
from models.operations import AnalyticsEvent
from repositories.users import UserRepository
from services.admin import StatsService
from services.onboarding import OnboardingService
from utils.time import utcnow


class FunnelTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The shared fixture seeds finished users; the funnel counts everyone,
        # so start from an empty cohort and add exactly the cases under test.
        async with self.factory.begin() as s:
            await s.execute(User.__table__.delete())

    async def signup(self, telegram_id, step="WELCOME", gender=None, age=None,
                     completed=False, days_ago=0):
        async with self.factory.begin() as s:
            s.add(User(telegram_id=telegram_id, anon_id=f"#f{telegram_id}",
                       onboarding_step=step, gender=gender, age=age,
                       onboarding_completed=completed,
                       rules_accepted_at=utcnow() if completed else None,
                       created_at=utcnow()-timedelta(days=days_ago)))

    async def test_each_stage_counts_everyone_who_reached_it(self):
        await self.signup(8001)                                        # stopped at welcome
        await self.signup(8002, step="GENDER")                         # pressed "Начать"
        await self.signup(8003, step="AGE", gender="male")             # gave gender
        await self.signup(8004, step="RULES", gender="female", age=22)  # gave age
        await self.signup(8005, step="MENU", gender="male", age=30, completed=True)
        funnel = await StatsService().onboarding_funnel(1)
        self.assertEqual([count for _, count in funnel["stages"]], [5, 4, 3, 2, 1])

    async def test_counts_stay_monotonic_when_a_step_is_skipped(self):
        # A user whose step column says MENU but who never answered the form
        # must not be counted past the stage its own columns support.
        await self.signup(8010, step="MENU")
        funnel = await StatsService().onboarding_funnel(1)
        self.assertEqual([count for _, count in funnel["stages"]], [1, 1, 0, 0, 0])

    async def test_older_cohorts_stay_out_of_the_period(self):
        await self.signup(8020, days_ago=9)
        await self.signup(8021)
        self.assertEqual((await StatsService().onboarding_funnel(1))["stages"][0][1], 1)
        self.assertEqual((await StatsService().onboarding_funnel(30))["stages"][0][1], 2)

    async def test_deleted_users_are_excluded(self):
        await self.signup(8030)
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 8030))
            user.deleted_at = utcnow()
        self.assertEqual((await StatsService().onboarding_funnel(1))["stages"][0][1], 0)

    async def test_an_underage_answer_is_counted_once_per_day(self):
        await UserRepository().get_or_create(8040, "u", "N")
        service = OnboardingService()
        await service.advance(8040, "begin")
        await service.advance(8040, "gender", "male")
        for _ in range(3):
            result = await service.advance(8040, "age", 15)
            self.assertEqual(result.code, "AGE")
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(AnalyticsEvent.id)).where(
                AnalyticsEvent.event == "onboarding_age_rejected")), 1)
        self.assertEqual((await StatsService().onboarding_funnel(1))["age_rejected"], 1)

    async def test_a_valid_age_leaves_no_rejection(self):
        await UserRepository().get_or_create(8041, "u", "N")
        service = OnboardingService()
        await service.advance(8041, "begin")
        await service.advance(8041, "gender", "female")
        await service.advance(8041, "age", 25)
        self.assertEqual((await StatsService().onboarding_funnel(1))["age_rejected"], 0)

    async def test_registration_time_is_reported_only_once_someone_finished(self):
        await self.signup(8050, step="GENDER")
        self.assertIsNone((await StatsService().onboarding_funnel(1))["avg_minutes"])
        await self.signup(8051, step="MENU", gender="male", age=20, completed=True)
        self.assertIsNotNone((await StatsService().onboarding_funnel(1))["avg_minutes"])

    async def test_the_daily_split_separates_the_cohorts(self):
        await self.signup(8060, days_ago=1)
        await self.signup(8061, step="MENU", gender="male", age=20, completed=True)
        daily = (await StatsService().onboarding_funnel(7))["daily"]
        self.assertEqual([(total, done) for _, total, done in daily], [(1, 0), (1, 1)])


class FunnelTextTests(unittest.TestCase):
    def render(self, stages, min_step_pct=0, **extra):
        data = dict(stages=stages, age_rejected=0, avg_minutes=None, daily=[])
        data.update(extra)
        return funnel_text(data, 7, min_step_pct)

    def test_each_stage_shows_its_share_of_the_whole_and_of_the_step_before(self):
        text = self.render([("Запустили /start", 100), ("Нажали «Начать»", 50),
                            ("Указали пол", 40), ("Указали возраст", 30),
                            ("Приняли правила", 20)])
        self.assertIn("Запустили /start: 100 (100% от всех)", text)
        self.assertIn("Нажали «Начать»: 50 (50% от всех, 50% от предыдущего)", text)
        self.assertIn("Не нажали ни одной кнопки после /start: 50", text)

    def test_an_empty_period_does_not_divide_by_zero(self):
        text = self.render([("Запустили /start", 0)]+[("x", 0)]*4)
        self.assertIn("никто не запускал бота", text)

    def test_a_stage_that_nobody_reached_does_not_divide_by_zero(self):
        text = self.render([("Запустили /start", 5), ("Нажали «Начать»", 0),
                            ("Указали пол", 0), ("Указали возраст", 0),
                            ("Приняли правила", 0)])
        self.assertIn("Указали пол: 0 (0% от всех, 0% от предыдущего)", text)

    def test_drop_off_warning_fires_below_the_configured_threshold(self):
        stages = [("Запустили /start", 100), ("Нажали «Начать»", 30),
                  ("Указали пол", 25), ("Указали возраст", 20),
                  ("Приняли правила", 15)]
        text = self.render(stages, min_step_pct=50)
        self.assertIn("⚠️ Просадка на шаге «Нажали «Начать»»: 30% от «Запустили /start» (порог 50%)", text)
        # Later steps clear the threshold and get no warning of their own.
        self.assertNotIn("шаге «Указали пол»", text)

    def test_no_warning_below_threshold_when_disabled(self):
        stages = [("Запустили /start", 100), ("Нажали «Начать»", 1)]+[("x", 1)]*3
        self.assertNotIn("Просадка", self.render(stages, min_step_pct=0))

    def test_no_warning_when_step_meets_the_threshold(self):
        stages = [("Запустили /start", 100), ("Нажали «Начать»", 80)]+[("x", 80)]*3
        self.assertNotIn("Просадка", self.render(stages, min_step_pct=50))

    def test_optional_lines_appear_only_when_there_is_something_to_say(self):
        stages = [("Запустили /start", 10)]+[("x", 5)]*4
        self.assertNotIn("возраста", self.render(stages))
        self.assertIn("Отклонено на шаге возраста: 3", self.render(stages, age_rejected=3))
        self.assertIn("Среднее время регистрации: 2.5 мин.", self.render(stages, avg_minutes=2.5))
        self.assertNotIn("По дням", self.render(stages, daily=[("2026-09-07", 10, 5)]))
        self.assertIn("2026-09-06: 10 / 5 (50%)",
                      self.render(stages, daily=[("2026-09-06", 10, 5), ("2026-09-07", 4, 0)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
