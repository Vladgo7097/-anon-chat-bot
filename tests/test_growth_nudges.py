"""Owner notice capping with an hourly digest, onboarding nudges, icebreakers."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import BotSetting, Notification
from repositories.users import UserRepository
from repositories.chats import icebreaker, ICEBREAKERS
from services.notifications import NotificationService
from utils.time import utcnow


class OwnerNoticeCapTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.OWNER_ID", 99)
        p.start()
        self.addCleanup(p.stop)

    async def signups(self, count, first_id=5000):
        for offset in range(count):
            await UserRepository().get_or_create(first_id+offset, f"u{offset}", "N")

    async def owner_notices(self):
        async with self.factory() as s:
            return await s.scalar(select(func.count(Notification.id)).where(
                Notification.type == "owner_new_user"))

    async def test_each_signup_is_reported_while_the_day_is_quiet(self):
        await self.signups(3)
        self.assertEqual(await self.owner_notices(), 3)

    async def test_the_flood_stops_at_the_daily_cap(self):
        async with self.factory.begin() as s:
            s.add(BotSetting(key="owner_notice_limit", value_json={"value": 5}))
        await self.signups(12)
        self.assertEqual(await self.owner_notices(), 5)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(User.id)).where(User.telegram_id >= 5000)), 12)

    async def test_digest_reports_only_what_was_not_sent_one_by_one(self):
        async with self.factory.begin() as s:
            s.add(BotSetting(key="owner_notice_limit", value_json={"value": 2}))
        await self.signups(9)
        # Move everything into the previous, completed hour.
        now = utcnow()
        hour_end = now.replace(minute=0, second=0, microsecond=0)
        stamp = hour_end - timedelta(minutes=30)
        async with self.factory.begin() as s:
            await s.execute(User.__table__.update().where(User.telegram_id >= 5000).values(created_at=stamp))
            await s.execute(Notification.__table__.update().where(
                Notification.type == "owner_new_user").values(scheduled_at=stamp))
        await NotificationService().owner_digest(now)
        async with self.factory() as s:
            digest = await s.scalar(select(Notification).where(Notification.type == "owner_digest"))
        self.assertIsNotNone(digest)
        self.assertEqual(digest.user_id, 99)
        self.assertIn("Новых пользователей: 9", digest.text)
        self.assertIn("поштучно показано 2", digest.text)

    async def test_a_quiet_hour_produces_no_digest(self):
        await NotificationService().owner_digest(utcnow())
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id)).where(
                Notification.type == "owner_digest")), 0)

    async def test_digest_is_written_once_per_hour(self):
        async with self.factory.begin() as s:
            s.add(BotSetting(key="owner_notice_limit", value_json={"value": 0}))
        await self.signups(4)
        now = utcnow()
        stamp = now.replace(minute=0, second=0, microsecond=0) - timedelta(minutes=30)
        async with self.factory.begin() as s:
            await s.execute(User.__table__.update().where(User.telegram_id >= 5000).values(created_at=stamp))
        service = NotificationService()
        await service.owner_digest(now)
        await service.owner_digest(now)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id)).where(
                Notification.type == "owner_digest")), 1)


class OnboardingNudgeTests(DatabaseCase):
    async def stuck_user(self, telegram_id=7001, hours_ago=5, completed=False):
        async with self.factory.begin() as s:
            s.add(User(telegram_id=telegram_id, anon_id=f"#stuck{telegram_id}",
                       onboarding_completed=completed, onboarding_step="WELCOME",
                       created_at=utcnow()-timedelta(hours=hours_ago),
                       last_online=utcnow()-timedelta(hours=hours_ago)))

    async def nudges(self):
        async with self.factory() as s:
            return (await s.scalars(select(Notification).where(
                Notification.type == "onboarding_nudge"))).all()

    async def test_user_stuck_past_the_delay_is_nudged_once(self):
        await self.stuck_user()
        service = NotificationService()
        await service.schedule()
        await service.schedule()
        rows = await self.nudges()
        self.assertEqual([n.user_id for n in rows], [7001])
        self.assertEqual(rows[0].callback_data, "menu")
        self.assertTrue(rows[0].marketing)

    async def test_a_fresh_signup_is_left_alone(self):
        await self.stuck_user(telegram_id=7002, hours_ago=0)
        await NotificationService().schedule()
        self.assertEqual(await self.nudges(), [])

    async def test_a_long_abandoned_signup_is_not_nudged(self):
        await self.stuck_user(telegram_id=7003, hours_ago=24*10)
        await NotificationService().schedule()
        self.assertEqual(await self.nudges(), [])

    async def test_marketing_optout_is_respected(self):
        await self.stuck_user(telegram_id=7004)
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 7004))
            user.notification_settings = '{"marketing": false}'
        await NotificationService().schedule()
        self.assertEqual(await self.nudges(), [])

    async def test_nudge_is_dropped_if_the_form_got_finished_meanwhile(self):
        await self.stuck_user(telegram_id=7005)
        service = NotificationService()
        await service.schedule()
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 7005))
            user.onboarding_completed = True
        self.assertIsNone(await service.next())
        async with self.factory() as s:
            row = await s.scalar(select(Notification).where(Notification.type == "onboarding_nudge"))
            self.assertEqual(row.status, "skipped")


class IcebreakerTests(DatabaseCase):
    def test_same_session_gives_both_sides_the_same_prompt(self):
        self.assertEqual(icebreaker("a1b2c3d4ffff0000"), icebreaker("a1b2c3d4ffff0000"))
        self.assertIn(icebreaker("a1b2c3d4ffff0000"), ICEBREAKERS)

    async def test_match_notice_carries_the_prompt_for_both_users(self):
        chat = await self.repo.match(1, 2, self.redis)
        async with self.factory() as s:
            rows = (await s.scalars(select(Notification).where(Notification.type == "match"))).all()
        self.assertEqual(len(rows), 2)
        starter = icebreaker(chat.session_id)
        for row in rows:
            self.assertIn("Собеседник найден", row.text)
            self.assertIn(starter, row.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
