import asyncio
from unittest.mock import patch
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import Notification
from repositories.users import UserRepository
from services.notifications import NotificationService
from services.onboarding import OnboardingService


class OwnerRegistrationTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        owner = patch("config.OWNER_ID", 99)
        owner.start()
        self.addCleanup(owner.stop)

    async def test_insert_notifies_before_onboarding_and_start_does_not_repeat(self):
        user, created = await UserRepository().get_or_create(4242, "new_person")
        self.assertTrue(created)
        self.assertFalse(user.onboarding_completed)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 1)
        for _ in range(3):
            await OnboardingService().start(4242, notify_owner=True)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 1)
        service = NotificationService()
        notice = await service.next()
        self.assertEqual(notice.user_id, 99)
        self.assertEqual(notice.type, "owner_new_user")
        self.assertIn("Юзернейм: @new_person", notice.text)
        self.assertIn("Всего пользователей: 4", notice.text)
        await service.finish(notice.id, "failed", "forbidden")
        self.assertIsNone(await service.next())

    async def test_existing_user_does_not_notify(self):
        await OnboardingService().start(1, notify_owner=True)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 0)

    async def test_concurrent_creation_queues_once(self):
        results = await asyncio.gather(*(UserRepository().get_or_create(4242) for _ in range(5)))
        self.assertEqual(sum(created for _, created in results), 1)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 1)

    async def test_notice_and_user_roll_back_together(self):
        with patch("services.notifications.enqueue", side_effect=RuntimeError("outbox unavailable")):
            with self.assertRaises(RuntimeError):
                await UserRepository().get_or_create(4242)
        async with self.factory() as s:
            self.assertIsNone(await s.scalar(select(User).where(User.telegram_id == 4242)))
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 0)

    async def test_owner_notices_are_paced(self):
        await UserRepository().get_or_create(4242)
        await UserRepository().get_or_create(4243)
        service = NotificationService()
        first = await service.next()
        await service.finish(first.id, "sent")
        self.assertIsNone(await service.next())
        async with self.factory() as s:
            pending = await s.scalar(select(Notification).where(Notification.status == "queued"))
            self.assertIsNotNone(pending)
            self.assertEqual(pending.attempt_count, 0)

    async def test_rate_limit_delays_all_recipient_notices(self):
        await UserRepository().get_or_create(4242)
        await UserRepository().get_or_create(4243)
        service = NotificationService()
        first = await service.next()
        await service.finish(first.id, "queued", "rate_limit", 193)
        self.assertIsNone(await service.next())
        async with self.factory() as s:
            notices = (await s.scalars(select(Notification))).all()
            self.assertEqual(len(notices), 2)
            self.assertTrue(all(n.status == "queued" for n in notices))

    async def test_missing_username(self):
        await UserRepository().get_or_create(4242)
        notice = await NotificationService().next()
        self.assertIn("Юзернейм: не указан", notice.text)
        self.assertIn("Всего пользователей: 4", notice.text)

    async def test_unconfigured_owner_does_not_queue(self):
        with patch("config.OWNER_ID", 0):
            await UserRepository().get_or_create(4242)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 0)
