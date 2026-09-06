from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.user import User
from models.report import Report
from models.ban import Ban
from models.operations import Notification, Broadcast, AdminAudit, Streak, Offer, Achievement, Referral
from services.admin import AdminService
from services.notifications import NotificationService, enqueue
from services.growth import StreakService
from services.reports import ReportService
from services.result import ServiceResult
from utils.time import utcnow


class OperationsTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for target, value in (("config.ADMIN_IDS",{99}),
            ("services.matching.MatchingService.cancel",AsyncMock()),
            ("services.chats.ChatService.end_chat",AsyncMock(return_value=ServiceResult(True,"ENDED")))):
            p=patch(target,value)
            p.start()
            self.addCleanup(p.stop)

    async def test_nonadmin_denied_and_invalid_setting_rejected(self):
        with self.assertRaises(PermissionError):
            await AdminService().setting(1,"search_timeout",60)
        self.assertFalse((await AdminService().setting(99,"search_timeout",1)).ok)
        self.assertTrue((await AdminService().setting(99,"search_timeout",90)).ok)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(AdminAudit.id))),1)

    async def test_find_user_accepts_anon_id_and_rejects_oversized_number(self):
        for value in ("#test1", "test1", "#TEST1", "1"):
            self.assertEqual((await AdminService().find_user(99, value)).telegram_id, 1)
        for value in ("9" * 4000, "²", "", "#missing"):
            self.assertIsNone(await AdminService().find_user(99, value))

    async def test_old_end_notification_does_not_interrupt_new_chat(self):
        old = await self.repo.match(1, 2, self.redis)
        await self.repo.end(1, old.session_id, "timeout")
        new = await self.repo.match(1, 3, self.redis)
        async with self.factory.begin() as s:
            items = (await s.scalars(select(Notification))).all()
            for item in items:
                if item.type == "chat_ended" and item.user_id == 1:
                    notice_id = item.id
                else:
                    item.status = "sent"
        self.assertIsNone(await NotificationService().next())
        async with self.factory() as s:
            self.assertEqual((await s.get(Notification, notice_id)).status, "skipped")
        self.assertEqual((await self.repo.active(1)).session_id, new.session_id)

    async def test_invalid_numeric_referral_does_not_break_registration(self):
        from services.onboarding import OnboardingService
        for value in ("9" * 32, "²"):
            result = await OnboardingService().start(4, payload="ref_" + value)
            self.assertEqual(result.code, "WELCOME")
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 4))
            self.assertIsNone(user.referrer_id)

    async def test_duplicate_admin_grant_does_not_extend_twice(self):
        admin=AdminService()
        self.assertTrue((await admin.grant(99,1,30,"support","grant:99:1")).ok)
        async with self.factory() as s:
            expires=(await s.scalar(select(User).where(User.telegram_id==1))).premium_expires
        self.assertFalse((await admin.grant(99,1,30,"support","grant:99:1")).ok)
        async with self.factory() as s:
            self.assertEqual((await s.scalar(select(User).where(User.telegram_id==1))).premium_expires,expires)

    async def test_ban_notice_delivered_and_unban_clears_history(self):
        await AdminService().ban(99,1,"spam",24)
        service=NotificationService()
        notification=await service.next()
        self.assertEqual(notification.type,"ban")
        await service.finish(notification.id,"sent")
        await AdminService().unban(99,1)
        async with self.factory() as s:
            self.assertFalse((await s.scalar(select(User).where(User.telegram_id==1))).is_banned)
            self.assertEqual(await s.scalar(select(func.count(Ban.id)).where(Ban.is_active.is_(True))),0)
        self.assertEqual((await service.next()).type,"unban")

    async def test_expired_ban_becomes_inactive_without_manual_action(self):
        async with self.factory.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==1))
            user.is_banned=True
            user.ban_expires=utcnow()-timedelta(seconds=1)
        await NotificationService().schedule()
        async with self.factory() as s:
            self.assertFalse((await s.scalar(select(User).where(User.telegram_id==1))).is_banned)
        self.assertEqual((await NotificationService().next()).type,"unban")

    async def test_report_saved_once_and_resolved_once(self):
        chat=await self.repo.match(1,2,self.redis)
        service=ReportService()
        first=await service.submit(1,chat.session_id,"spam","test details")
        self.assertTrue(first.ok)
        self.assertEqual((await service.submit(1,chat.session_id,"spam")).code,"ALREADY_REPORTED")
        self.assertTrue((await AdminService().resolve(99,first.data["report_id"],"dismiss")).ok)
        self.assertFalse((await AdminService().resolve(99,first.data["report_id"],"ban1")).ok)

    async def test_broadcast_confirm_once_worker_counts_once_and_respects_optout(self):
        async with self.factory.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==2))
            user.notification_settings='{"marketing":false}'
        admin=AdminService()
        job=(await admin.broadcast(99,"all","Hello")).data["job"]
        self.assertTrue((await admin.confirm_broadcast(99,job.id)).ok)
        self.assertFalse((await admin.confirm_broadcast(99,job.id)).ok)
        service=NotificationService()
        await service.expand_broadcast()
        first=await service.next()
        self.assertEqual(first.user_id,1)
        await service.finish(first.id,"sent")
        await service.finish(first.id,"sent")
        self.assertIsNone(await service.next())
        third=await service.next()
        self.assertEqual(third.user_id,3)
        await service.finish(third.id,"failed","forbidden")
        await service.expand_broadcast()
        async with self.factory() as s:
            job=await s.get(Broadcast,job.id)
            self.assertEqual((job.sent,job.failed,job.blocked,job.status),(1,1,1,"completed"))
            self.assertIsNotNone((await s.scalar(select(User).where(User.telegram_id==3))).bot_blocked_at)

    async def test_streak_utc_reward_once(self):
        async with self.factory.begin() as s:
            s.add(Streak(user_id=1,current_streak=6,best_streak=6,last_checkin_date=utcnow().date()-timedelta(days=1)))
        self.assertEqual(await StreakService().checkin(1),7)
        self.assertEqual(await StreakService().checkin(1),7)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Offer.id))),2)
            self.assertEqual(await s.scalar(select(func.count()).select_from(Achievement)),2)

    async def test_season_requires_enabled_flag_and_real_window(self):
        from services.growth import SeasonalService
        from models.operations import BotSetting
        admin=AdminService()
        self.assertFalse((await admin.seasonal(99,"autumn","invalid","invalid",15)).ok)
        now=utcnow()
        self.assertTrue((await admin.seasonal(99,"autumn",(now-timedelta(hours=1)).isoformat(),(now+timedelta(hours=1)).isoformat(),15)).ok)
        async with self.factory.begin() as s:
            self.assertIsNone(await SeasonalService.active(s))
        await admin.setting(99,"seasonal_enabled",1)
        async with self.factory.begin() as s:
            await SeasonalService.reward(s,1)
            await SeasonalService.reward(s,1)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Offer.id))),1)
        async with self.factory.begin() as s:
            item=await s.get(BotSetting,"seasonal_event")
            item.value_json={**item.value_json,"ends_at":(now-timedelta(seconds=1)).isoformat()}
        async with self.factory.begin() as s:
            self.assertIsNone(await SeasonalService.active(s))

    async def test_marketing_daily_limit_defers_second_delivery(self):
        async with self.factory.begin() as s:
            await enqueue(s,1,"campaign","campaign:1","First",marketing=True)
            await enqueue(s,1,"campaign","campaign:2","Second",marketing=True)
        service=NotificationService()
        first=await service.next()
        await service.finish(first.id,"sent")
        self.assertIsNone(await service.next())
        async with self.factory() as s:
            pending=await s.scalar(select(Notification).where(Notification.dedupe_key=="campaign:2"))
            self.assertGreater(pending.scheduled_at,utcnow()+timedelta(hours=23))

    async def test_profile_photos_and_server_side_age_limits(self):
        from services.profile import ProfileService
        service=ProfileService()
        self.assertFalse((await service.edit(1,"age",17)).ok)
        self.assertTrue((await service.add_photo(1,"photo-one")).ok)
        self.assertTrue((await service.add_photo(1,"photo-one")).ok)
        self.assertEqual((await service.add_photo(1,"photo-two")).code,"PHOTO_LIMIT")
        await AdminService().grant(99,1,30,"test")
        self.assertTrue((await service.add_photo(1,"photo-two")).ok)
        self.assertTrue((await service.add_photo(1,"photo-three")).ok)
        self.assertFalse((await service.add_photo(1,"photo-four")).ok)

    async def test_referral_reward_once_after_real_completed_chat(self):
        async with self.factory.begin() as s:
            s.add(Referral(referrer_id=3,referred_id=1))
        chat=await self.repo.match(1,2,self.redis)
        await self.repo.end(1,chat.session_id)
        await self.repo.end(2,chat.session_id)
        async with self.factory() as s:
            self.assertEqual((await s.scalar(select(User).where(User.telegram_id==3))).referrals_activated,1)
            self.assertEqual(await s.scalar(select(func.count(Offer.id))),2)


class PaymentFloodTests(__import__('unittest').IsolatedAsyncioTestCase):
    async def test_successful_payment_never_dropped_by_flood_limiter(self):
        from utils.middleware import AntiFloodMiddleware
        handler=AsyncMock()
        event=SimpleNamespace(successful_payment=object(),from_user=SimpleNamespace(id=1))
        with patch("utils.middleware.check_flood",AsyncMock(return_value=True)) as flood:
            await AntiFloodMiddleware()(handler,event,{})
        handler.assert_awaited_once()
        flood.assert_not_awaited()
