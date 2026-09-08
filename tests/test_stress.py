"""Load and contention tests.

Gated behind RUN_STRESS_TESTS=1 because they build hundreds of rows and run
concurrent transactions; they are meant for a sized stand, never for the host
that also serves production.
"""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.chat import Chat, ActiveParticipant
from models.operations import Broadcast, Notification
from models.user import User
from services.admin import AdminService
from services.chats import ChatService
from services.matching import MatchingService
from services.notifications import NotificationService
from utils.time import utcnow

STRESS = os.getenv("RUN_STRESS_TESTS") == "1"
POSTGRES = os.getenv("RUN_POSTGRES_TESTS") == "1"


def rss_mb():
    """Peak resident memory; the stand is Linux, Windows collection must not break."""
    try:
        import resource
    except ImportError:
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return usage/1024 if sys.platform != "darwin" else usage/1024/1024


@unittest.skipUnless(STRESS and POSTGRES, "stress suite needs RUN_STRESS_TESTS=1 and PostgreSQL")
class MatchingStressTests(DatabaseCase):
    USERS = 40

    async def asyncSetUp(self):
        await super().asyncSetUp()
        import redis.asyncio as redis
        self.real_redis = redis.from_url(os.getenv("REDIS_URL", "redis://test-redis:6379/0"),
                                         decode_responses=True)
        await self.real_redis.flushdb()
        p = patch("services.chat_manager.get_redis", return_value=self.real_redis)
        p.start()
        self.addCleanup(p.stop)
        async with self.factory.begin() as s:
            for uid in range(100, 100+self.USERS):
                s.add(User(telegram_id=uid, anon_id=f"#s{uid}", gender="male" if uid % 2 else "female",
                           age=25, onboarding_completed=True, onboarding_step="MENU",
                           rules_version=1, rules_accepted_at=utcnow()))

    async def asyncTearDown(self):
        await self.real_redis.aclose()
        await super().asyncTearDown()

    async def test_concurrent_search_never_puts_a_user_in_two_chats(self):
        ids = list(range(100, 100+self.USERS))
        matching = MatchingService()
        started = time.monotonic()
        await asyncio.gather(*(matching.enqueue(uid) for uid in ids))
        # Everyone searches at once, twice, the way a burst of taps would look.
        await asyncio.gather(*(matching.find(uid) for uid in ids), return_exceptions=False)
        await asyncio.gather(*(matching.find(uid) for uid in ids), return_exceptions=False)
        elapsed = time.monotonic()-started
        async with self.factory() as s:
            rows = (await s.execute(select(ActiveParticipant.user_id, func.count())
                                    .group_by(ActiveParticipant.user_id))).all()
            doubled = [uid for uid, count in rows if count > 1]
            chats = await s.scalar(select(func.count(Chat.id)).where(Chat.status == "active"))
            participants = await s.scalar(select(func.count()).select_from(ActiveParticipant))
        self.assertEqual(doubled, [], "a user ended up in more than one active chat")
        self.assertEqual(participants, chats*2, "an active chat is missing a participant")
        self.assertLessEqual(chats, self.USERS//2)
        print(f"\n[stress] {self.USERS} искателей -> {chats} диалогов за {elapsed:.1f} c")

    async def test_concurrent_stop_ends_a_chat_exactly_once(self):
        matching = MatchingService()
        await asyncio.gather(*(matching.enqueue(uid) for uid in (100, 101)))
        chat = await matching.find(100)
        self.assertIsNotNone(chat)
        service = ChatService()
        results = await asyncio.gather(*[service.end_chat(100 if i % 2 else 101, reason="stop")
                                         for i in range(12)], return_exceptions=True)
        ok = [r for r in results if not isinstance(r, Exception) and r.ok]
        self.assertEqual(len(ok), 1, "a chat was ended more than once")
        async with self.factory() as s:
            for uid in (100, 101):
                user = await s.scalar(select(User).where(User.telegram_id == uid))
                self.assertEqual(user.chats_count, 1)


@unittest.skipUnless(STRESS and POSTGRES, "stress suite needs RUN_STRESS_TESTS=1 and PostgreSQL")
class BroadcastStressTests(DatabaseCase):
    USERS = 200

    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.ADMIN_IDS", {99})
        p.start()
        self.addCleanup(p.stop)
        async with self.factory.begin() as s:
            for uid in range(1000, 1000+self.USERS):
                s.add(User(telegram_id=uid, anon_id=f"#b{uid}", gender="male", age=25,
                           onboarding_completed=True, onboarding_step="MENU",
                           rules_version=1, rules_accepted_at=utcnow()))

    async def test_broadcast_reaches_every_recipient_exactly_once(self):
        admin = AdminService()
        expected = await admin.audience_size(99, "all")
        self.assertEqual(expected, self.USERS+3)  # +3 fixture users
        job = (await admin.broadcast(99, "all", "Массовое сообщение")).data["job"]
        confirmed = (await admin.confirm_broadcast(99, job.id)).data["job"]
        self.assertEqual(confirmed.total, expected)
        service = NotificationService()
        started = time.monotonic()
        for _ in range(40):
            await service.expand_broadcast()
            async with self.factory() as s:
                if (await s.get(Broadcast, job.id)).status != "queued":
                    break
        expansion = time.monotonic()-started
        async with self.factory() as s:
            queued = await s.scalar(select(func.count(Notification.id))
                                    .where(Notification.broadcast_id == job.id))
            distinct = await s.scalar(select(func.count(func.distinct(Notification.user_id)))
                                      .where(Notification.broadcast_id == job.id))
        self.assertEqual(queued, expected, "audience snapshot and queue disagree")
        self.assertEqual(distinct, expected, "a recipient was queued twice")
        delivery = time.monotonic()
        delivered = 0
        for _ in range(expected*3):
            item = await service.next()
            if not item:
                async with self.factory() as s:
                    pending = await s.scalar(select(func.count(Notification.id)).where(
                        Notification.broadcast_id == job.id, Notification.status.in_(["queued", "sending"])))
                if not pending:
                    break
            else:
                await service.finish(item.id, "sent")
                delivered += 1
        await service.expand_broadcast()
        found = await admin.broadcast_progress(99, job.id)
        stored, counts = found
        self.assertEqual(stored.status, "completed")
        self.assertEqual(counts.get("sent"), expected)
        from handlers.admin import broadcast_card
        self.assertIn("100%", broadcast_card(stored, counts))
        print(f"\n[stress] рассылка на {expected}: разворот {expansion:.1f} c, "
              f"доставка {time.monotonic()-delivery:.1f} c, отправлено {delivered}")

    async def test_scheduler_stays_bounded_on_a_large_user_table(self):
        from sqlalchemy import event
        queries = []

        def count(conn, cursor, statement, parameters, context, many):
            queries.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", count)
        try:
            started = time.monotonic()
            await NotificationService().schedule()
            elapsed = time.monotonic()-started
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", count)
        # Batches of 100 with a fixed number of statements each, not one per user.
        self.assertLess(len(queries), self.USERS,
                        f"scheduler issued {len(queries)} queries for {self.USERS} users")
        print(f"\n[stress] планировщик на {self.USERS+3} пользователей: "
              f"{len(queries)} запросов за {elapsed:.1f} c")


@unittest.skipUnless(STRESS, "stress suite needs RUN_STRESS_TESTS=1")
class RelayStressTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        import redis.asyncio as redis
        self.real_redis = redis.from_url(os.getenv("REDIS_URL", "redis://test-redis:6379/0"),
                                         decode_responses=True)
        await self.real_redis.flushdb()
        p = patch("services.chat_manager.get_redis", return_value=self.real_redis)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.real_redis.aclose()
        await super().asyncTearDown()

    async def test_reply_map_stays_capped_under_a_long_dialogue(self):
        from services.relay import RelayService
        relay = RelayService()
        before = rss_mb()
        for message_id in range(1, 1201):
            await relay.remember("stress-session", 1, message_id, 2, 100000+message_id)
        size = await self.real_redis.hlen("relay:map:stress-session")
        ttl = await self.real_redis.ttl("relay:map:stress-session")
        # 1200 messages create 2400 fields; the map must stay bounded.
        self.assertLessEqual(size, 2100, f"reply map grew to {size} fields")
        self.assertGreater(ttl, 0, "reply map has no expiry")
        # The newest message must still resolve after trimming.
        self.assertEqual(await relay.reply_target("stress-session", 1, 1200), 101200)
        print(f"\n[stress] карта ответов: {size} полей, TTL {ttl} c, "
              f"RSS {before:.0f} -> {rss_mb():.0f} МБ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
