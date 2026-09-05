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


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "isolated PostgreSQL/Redis only")
class ConcurrentTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        import redis.asyncio as redis
        self.real_redis=redis.from_url("redis://test-redis:6379/0",decode_responses=True)
        await self.real_redis.flushdb()
        p=patch("services.chat_manager.get_redis",return_value=self.real_redis)
        p.start()
        self.addCleanup(p.stop)
        await seed_products()

    async def asyncTearDown(self):
        await self.real_redis.aclose()
        await super().asyncTearDown()

    async def test_double_enqueue_competing_matches_and_double_stop(self):
        matching=MatchingService()
        results=await asyncio.gather(*(matching.enqueue(1) for _ in range(5)))
        self.assertEqual(sum(r.code=="SEARCH_STARTED" for r in results),1)
        await matching.enqueue(2)
        await matching.enqueue(3)
        results=await asyncio.gather(self.repo.match(1,2,self.real_redis),self.repo.match(1,3,self.real_redis))
        self.assertEqual(sum(c is not None for c in results),1)
        chat=next(c for c in results if c)
        await ChatService().sync(chat)
        ended=await asyncio.gather(*(ChatService().end_chat(1,chat.session_id) for _ in range(5)))
        self.assertEqual(sum(r.ok for r in ended),1)
        self.assertIsNone(await self.real_redis.get("active_chat:1"))
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Chat.id))),1)
            self.assertEqual(await s.scalar(select(func.count()).select_from(ActiveParticipant)),0)

    async def test_duplicate_successful_payment(self):
        payments=PaymentService()
        p=(await payments.create(1,"instant_search")).data["payment"]
        self.assertTrue(await payments.precheckout(1,p.payload,p.stars_amount,"XTR"))
        results=await asyncio.gather(*(payments.complete(1,p.payload,p.stars_amount,"XTR","one-charge") for _ in range(5)))
        self.assertEqual(sum(r.ok for r in results),1)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Entitlement.id))),1)

    async def test_two_moderators_resolve_once(self):
        await MatchingService().enqueue(1)
        await MatchingService().enqueue(2)
        chat=await self.repo.match(1,2,self.real_redis)
        report=await ReportService().submit(1,chat.session_id,"spam")
        with patch("config.ADMIN_IDS",{10,11}):
            results=await asyncio.gather(AdminService().resolve(10,report.data["report_id"],"ban1"),AdminService().resolve(11,report.data["report_id"],"dismiss"))
        self.assertEqual(sum(r.ok for r in results),1)

    async def test_restart_repairs_active_cache_without_duplicate_pair(self):
        await MatchingService().enqueue(1)
        await MatchingService().enqueue(2)
        chat=await self.repo.match(1,2,self.real_redis)
        await self.real_redis.flushdb()
        await ChatService().reconcile()
        self.assertEqual(await self.real_redis.get("active_session:1"),chat.session_id)
        self.assertEqual((await MatchingService().enqueue(1)).code,"ALREADY_CHATTING")

    async def test_application_startup_and_dependency_healthcheck(self):
        from pathlib import Path
        import bot
        import utils.healthcheck as health
        heartbeat=Path(self.temp.name)/"heartbeat"
        heartbeat.write_text("test")
        with patch.object(bot,"engine",self.engine):
            await bot.on_startup()
        with patch.object(health,"engine",self.engine),patch.object(health,"Path",return_value=heartbeat):
            await health.check()
