import asyncio
import os
import unittest
from unittest.mock import patch

from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.chat import Chat, ActiveParticipant
from models.payment import Entitlement, Payment
from services.payments import PaymentService, seed_products
from services.matching import MatchingService
from services.chats import ChatService
from services.reports import ReportService
from services.admin import AdminService


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_TESTS") == "1", "isolated PostgreSQL/Redis only"
)
class ConcurrentTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        import redis.asyncio as redis
        import config

        self.real_redis = redis.from_url(
            config.REDIS_URL, decode_responses=True
        )
        await self.real_redis.flushdb()
        p = patch("services.chat_manager.get_redis", return_value=self.real_redis)
        p.start()
        self.addCleanup(p.stop)
        await seed_products()

    async def asyncTearDown(self):
        await self.real_redis.aclose()
        await super().asyncTearDown()

    async def test_growth_unique_grants_are_safe_under_contention(self):
        from services.growth import award, event, first_purchase_discount, offer
        from models.operations import Achievement, AnalyticsEvent, Offer as GrowthOffer

        async def grant_once():
            async with self.factory.begin() as session:
                await event(session, 1, "concurrent_event", "growth:event:shared")
                await award(session, 1, "concurrent_award")
                await offer(session, 1, "growth:shared_offer", 20)

        results = await asyncio.gather(
            *(grant_once() for _ in range(8)), return_exceptions=True
        )
        self.assertEqual(
            [error for error in results if isinstance(error, BaseException)], []
        )

        discounts = await asyncio.gather(
            *(first_purchase_discount(1) for _ in range(8)),
            return_exceptions=True,
        )
        self.assertEqual(discounts, [50] * 8)

        async with self.factory() as session:
            self.assertEqual(
                await session.scalar(
                    select(func.count(AnalyticsEvent.id)).where(
                        AnalyticsEvent.dedupe_key == "growth:event:shared"
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(Achievement.user_id)).where(
                        Achievement.user_id == 1,
                        Achievement.achievement_code == "concurrent_award",
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(GrowthOffer.id)).where(
                        GrowthOffer.code == "growth:shared_offer"
                    )
                ),
                1,
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count(GrowthOffer.id)).where(
                        GrowthOffer.code == "first_purchase:1"
                    )
                ),
                1,
            )

    async def test_concurrent_friend_accepts_cannot_exceed_limit(self):
        from models.community import Friendship
        from services.community import FriendService

        async with self.factory.begin() as session:
            first = Friendship(
                user1_id=1,
                user2_id=2,
                requested_by=2,
                status="pending",
            )
            second = Friendship(
                user1_id=1,
                user2_id=3,
                requested_by=3,
                status="pending",
            )
            session.add_all([first, second])
            await session.flush()
            first_id, second_id = first.id, second.id

        with patch("config.MAX_FRIENDS", 1):
            results = await asyncio.gather(
                FriendService().respond(1, first_id, True),
                FriendService().respond(1, second_id, True),
            )
        self.assertEqual(sum(result.ok for result in results), 1)
        self.assertEqual(
            sum(result.code == "LIMIT" for result in results), 1
        )
        async with self.factory() as session:
            accepted = await session.scalar(
                select(func.count(Friendship.id)).where(
                    Friendship.user1_id == 1,
                    Friendship.status == "accepted",
                )
            )
            self.assertEqual(accepted, 1)

    async def test_scheduler_runs_while_matching_lock_is_held(self):
        from sqlalchemy import text
        from services.notifications import NotificationService

        async with self.engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(8873771496)"))
            await asyncio.wait_for(NotificationService().schedule(), timeout=10)

    async def test_reply_mapping_is_bidirectional_and_session_scoped(self):
        from services.relay import RelayService

        relay = RelayService()
        await relay.remember("first", 1, 10, 2, 90)
        self.assertEqual(await relay.reply_target("first", 1, 10), 90)
        self.assertEqual(await relay.reply_target("first", 2, 90), 10)
        self.assertIsNone(await relay.reply_target("second", 2, 90))
        self.assertGreater(await self.real_redis.ttl("relay:map:first"), 0)

    async def test_prefilter_reaches_later_page_without_locking_incompatible_pairs(
        self,
    ):
        from datetime import timedelta
        from models.user import User
        from utils.time import utcnow
        from repositories.chats import lock_matching
        from unittest.mock import AsyncMock

        async with self.factory.begin() as s:
            own = await s.scalar(select(User).where(User.telegram_id == 1))
            own.premium_expires = utcnow() + timedelta(days=1)
            own.settings_gender_filter = "f"
            for uid in range(100, 350):
                s.add(
                    User(
                        telegram_id=uid,
                        anon_id=f"#bulk{uid}",
                        gender="male",
                        age=23,
                        onboarding_completed=True,
                        onboarding_step="MENU",
                        rules_version=1,
                        rules_accepted_at=utcnow(),
                    )
                )
        async with self.real_redis.pipeline(transaction=True) as pipe:
            pipe.zadd("search_queue", {str(uid): uid for uid in [1, *range(100, 350)]})
            for uid in [1, 2, *range(100, 350)]:
                pipe.set(f"search:user:{uid}", "1", ex=120)
            await pipe.execute()
        with patch(
            "repositories.chats.lock_matching", AsyncMock(wraps=lock_matching)
        ) as lock:
            self.assertIsNone(await MatchingService().find(1))
            lock.assert_not_awaited()
            await self.real_redis.zadd("search_queue", {"2": 999})
            chat = await MatchingService().find(1)
            self.assertIsNotNone(chat)
            self.assertEqual(chat.user2_id, 2)
            # One final pair validation, then one cache sync guarded against
            # a concurrent stop. Neither lock is acquired per rejected candidate.
            self.assertEqual(lock.await_count, 2)

    async def test_double_enqueue_competing_matches_and_double_stop(self):
        matching = MatchingService()
        await self.real_redis.set("search:waiting_alert:1", "1", ex=600)
        await self.real_redis.sadd("search:waiting_alerts", "1")
        results = await asyncio.gather(*(matching.enqueue(1) for _ in range(5)))
        self.assertEqual(sum(r.code == "SEARCH_STARTED" for r in results), 1)
        # Opting into the wait list, then searching, drops the opt-in so the
        # next newcomer does not ping the user about their own search.
        self.assertIsNone(await self.real_redis.get("search:waiting_alert:1"))
        self.assertFalse(await self.real_redis.sismember("search:waiting_alerts", "1"))
        await matching.enqueue(2)
        await matching.enqueue(3)
        results = await asyncio.gather(
            self.repo.match(1, 2, self.real_redis),
            self.repo.match(1, 3, self.real_redis),
        )
        self.assertEqual(sum(c is not None for c in results), 1)
        chat = next(c for c in results if c)
        await ChatService().sync(chat)
        ended = await asyncio.gather(
            *(ChatService().end_chat(1, chat.session_id) for _ in range(5))
        )
        self.assertEqual(sum(r.ok for r in ended), 1)
        self.assertIsNone(await self.real_redis.get("active_chat:1"))
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Chat.id))), 1)
            self.assertEqual(
                await s.scalar(select(func.count()).select_from(ActiveParticipant)), 0
            )

    async def test_duplicate_successful_payment(self):
        payments = PaymentService()
        p = (await payments.create(1, "instant_search")).data["payment"]
        self.assertTrue(await payments.precheckout(1, p.payload, p.stars_amount, "XTR"))
        results = await asyncio.gather(
            *(
                payments.complete(1, p.payload, p.stars_amount, "XTR", "one-charge")
                for _ in range(5)
            )
        )
        self.assertEqual(sum(r.ok for r in results), 1)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Entitlement.id))), 1)

    async def test_two_moderators_resolve_once(self):
        await MatchingService().enqueue(1)
        await MatchingService().enqueue(2)
        chat = await self.repo.match(1, 2, self.real_redis)
        report = await ReportService().submit(1, chat.session_id, "spam")
        with patch("config.ADMIN_IDS", {10, 11}):
            results = await asyncio.gather(
                AdminService().resolve(10, report.data["report_id"], "ban1"),
                AdminService().resolve(11, report.data["report_id"], "dismiss"),
            )
        self.assertEqual(sum(r.ok for r in results), 1)

    async def test_restart_repairs_active_cache_without_duplicate_pair(self):
        await MatchingService().enqueue(1)
        await MatchingService().enqueue(2)
        chat = await self.repo.match(1, 2, self.real_redis)
        await self.real_redis.flushdb()
        await ChatService().reconcile()
        self.assertEqual(await self.real_redis.get("active_session:1"), chat.session_id)
        self.assertEqual((await MatchingService().enqueue(1)).code, "ALREADY_CHATTING")

    async def test_application_startup_and_dependency_healthcheck(self):
        from pathlib import Path
        import bot
        import utils.healthcheck as health

        heartbeat = Path(self.temp.name) / "heartbeat"
        heartbeat.write_text("test")
        with patch.object(bot, "engine", self.engine):
            await bot.on_startup()
        with (
            patch.object(health, "engine", self.engine),
            patch.object(health, "Path", return_value=heartbeat),
        ):
            await health.check()
