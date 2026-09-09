"""Service integration tests using an isolated database, never the live database."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch, AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, func
from models.base import Base
from models.user import User
from models.chat import Chat, ActiveParticipant, Rating, Block
import models.payment, models.report, models.operations
from repositories.chats import ChatRepository, compatible
from services.reveal import RevealService
from utils.time import utcnow


class DatabaseCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        url="sqlite+aiosqlite:///" + str(Path(self.temp.name)/"test.db")
        if os.getenv("RUN_POSTGRES_TESTS") == "1":
            import config
            from sqlalchemy.engine import make_url
            url=config.DATABASE_URL
            if make_url(url).database != "anon_chat_test" or make_url(url).host != "test-db":
                raise RuntimeError("Integration tests require the isolated test-db/anon_chat_test")
        self.engine = create_async_engine(url)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in ("models.base", "repositories.chats", "repositories.users", "services.reveal", "services.reports", "services.admin", "services.notifications", "services.payments", "services.growth", "services.profile", "services.settings", "services.matching", "services.ads"):
            p = patch(module + ".async_session", self.factory)
            p.start()
            self.addCleanup(p.stop)
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.drop_all)
            await c.run_sync(Base.metadata.create_all)
        async with self.factory.begin() as s:
            for uid, gender in ((1, "male"), (2, "female"), (3, "male")):
                s.add(User(telegram_id=uid, anon_id=f"#test{uid}", gender=gender, age=23,
                    onboarding_completed=True, onboarding_step="MENU", rules_version=1, rules_accepted_at=utcnow(),
                    total_chat_seconds=100))
        self.redis = AsyncMock()
        self.redis.zscore.return_value = 1
        self.repo = ChatRepository()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()


class SessionTests(DatabaseCase):
    async def test_duration_and_duplicate_end_and_rating(self):
        chat = await self.repo.match(1, 2, self.redis)
        self.assertIsNotNone(chat)
        async with self.factory.begin() as s:
            c = await s.get(Chat, chat.id)
            c.started_at = utcnow()-timedelta(seconds=60)
        ended = await self.repo.end(1, chat.session_id)
        self.assertGreaterEqual(ended.duration_seconds, 60)
        self.assertIsNone(await self.repo.end(2, chat.session_id))
        self.assertTrue(await self.repo.rate(chat.session_id, 1, 1))
        self.assertTrue(await self.repo.rate(chat.session_id, 2, -1))
        self.assertFalse(await self.repo.rate(chat.session_id, 1, -1))
        self.assertFalse(await self.repo.rate(chat.session_id, 3, 1))
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Chat.id))), 1)
            self.assertEqual(await s.scalar(select(func.count()).select_from(ActiveParticipant)), 0)
            for u in (await s.scalars(select(User).where(User.telegram_id.in_([1,2])))).all():
                self.assertEqual(u.chats_count, 1)
                self.assertEqual(u.total_chat_seconds, 100+ended.duration_seconds)

    async def test_no_double_pair_and_no_cancelled_match(self):
        self.assertIsNotNone(await self.repo.match(1, 2, self.redis))
        self.assertIsNone(await self.repo.match(1, 3, self.redis))
        self.redis.zscore.return_value = None
        self.assertIsNone(await self.repo.match(2, 3, self.redis))

    async def test_blocks_filters_and_age(self):
        async with self.factory.begin() as s:
            s.add(Block(blocker_id=2, blocked_id=1))
        self.assertIsNone(await self.repo.match(1, 2, self.redis))
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id==3))
            user.premium_expires = utcnow()+timedelta(days=1)
            user.settings_gender_filter = "f"
        self.assertIsNone(await self.repo.match(1, 3, self.redis))
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id==3))
            user.age = 17
        self.assertIsNone(await self.repo.match(2, 3, self.redis))

    async def test_consent_is_session_bound_and_mutual(self):
        chat = await self.repo.match(1, 2, self.redis)
        reveal = RevealService()
        self.assertFalse((await reveal.consent(3, chat.session_id)).ok)
        self.assertEqual((await reveal.consent(1, chat.session_id)).code, "PENDING")
        self.assertEqual((await reveal.consent(1, chat.session_id)).code, "ALREADY_REQUESTED")
        self.assertEqual((await reveal.consent(2, chat.session_id)).code, "MUTUAL")
        await self.repo.end(1, chat.session_id)
        new = await self.repo.match(1, 2, self.redis)
        self.assertEqual((await reveal.consent(1, new.session_id)).code, "PENDING")

    async def test_reveal_can_be_declined_and_returns_mutual_photo(self):
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 1))
            user.profile_photo = "safe-file-id"
        chat = await self.repo.match(1, 2, self.redis)
        reveal = RevealService()
        await reveal.consent(1, chat.session_id)
        declined = await reveal.decline(2, chat.session_id)
        self.assertEqual(declined.code, "DECLINED")
        self.assertEqual((await reveal.status(1, chat.session_id)).code, "DECLINED")
        self.assertFalse((await reveal.consent(2, chat.session_id)).ok)

        await self.repo.end(1, chat.session_id)
        second = await self.repo.match(1, 2, self.redis)
        await reveal.consent(1, second.session_id)
        mutual = await reveal.consent(2, second.session_id)
        self.assertEqual(mutual.data["profiles"][1]["photo"], "safe-file-id")


if __name__ == "__main__":
    unittest.main(verbosity=2)
