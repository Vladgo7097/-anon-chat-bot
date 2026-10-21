"""Community features: progressive warnings, report snapshots, friendships,
partner return and the referral OP bypass.

The database is the same throwaway SQLite factory used by DatabaseCase; Redis
is a small AsyncMock so nothing touches a live server.
"""

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from test_chat_duration import DatabaseCase
from models.community import Friendship, ReconnectOffer, UserWarning
from models.operations import Notification, Referral
from models.op import OpExemption
from models.report import Report, ReportSnapshot
from models.user import User
from services.admin import AdminService
from services.community import FriendService, ReconnectService
from services.growth import grant_op_bypass
from services.moderation import add_warning, apply_progressive, warning_count
from services.relay import RelayService
from services.reports import ReportService, cleanup as cleanup_reports
from utils.time import utcnow


class CommunityCase(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.admin = patch("config.ADMIN_IDS", {99})
        self.admin.start()
        self.addCleanup(self.admin.stop)
        self.fake_redis = AsyncMock()
        self.fake_redis.set.return_value = True
        self.fake_redis.zrem.return_value = 1
        self.fake_redis.delete.return_value = 1
        self.redis_patch = patch(
            "services.chat_manager.get_redis", AsyncMock(return_value=self.fake_redis)
        )
        self.redis_patch.start()
        self.addCleanup(self.redis_patch.stop)
        self.sync_patch = patch("services.community.ChatService.sync", AsyncMock())
        self.sync_patch.start()
        self.addCleanup(self.sync_patch.stop)

    async def ended_chat(self, a=1, b=2, reason="stop"):
        chat = await self.repo.match(a, b, self.redis)
        self.assertIsNotNone(chat)
        ended = await self.repo.end(a, chat.session_id, reason)
        self.assertIsNotNone(ended)
        return ended


class WarningTests(CommunityCase):
    async def test_confirmed_warning_stays_a_warning_below_the_threshold(self):
        chat = await self.repo.match(1, 2, self.redis)
        report = await ReportService().submit(1, chat.session_id, "spam")
        self.assertTrue(report.ok)
        result = await AdminService().resolve(99, report.data["report_id"], "warn")
        self.assertTrue(result.ok)
        self.assertIsNone(result.data["penalty"])
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 2))
            count = await warning_count(s, 2)
        self.assertFalse(user.is_muted)
        self.assertEqual(count, 1)

    async def test_three_warnings_mute_and_five_ban(self):
        async with self.factory.begin() as s:
            for _ in range(3):
                await add_warning(s, 2, "spam")
            self.assertEqual(await apply_progressive(s, 2), "mute")
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 2))
            self.assertTrue(user.is_muted)
            self.assertGreater(user.mute_expires, utcnow())
        async with self.factory.begin() as s:
            for _ in range(2):
                await add_warning(s, 2, "spam")
            self.assertEqual(await apply_progressive(s, 2), "ban")
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 2))
            self.assertTrue(user.is_banned)
            self.assertGreater(user.ban_expires, utcnow())

    async def test_expired_warning_does_not_count(self):
        async with self.factory.begin() as s:
            s.add(
                UserWarning(
                    user_id=2,
                    reason="old",
                    created_at=utcnow() - timedelta(days=40),
                    expires_at=utcnow() - timedelta(days=1),
                )
            )
        async with self.factory() as s:
            self.assertEqual(await warning_count(s, 2), 0)


class ReportSnapshotTests(CommunityCase):
    async def test_report_saves_media_and_text_snapshot_with_ttl(self):
        chat = await self.repo.match(1, 2, self.redis)
        context = [
            {"user_id": 1, "kind": "text", "file_id": None, "text": "привет"},
            {"user_id": 2, "kind": "photo", "file_id": "AgAC-photo", "text": "смотри"},
        ]
        with patch(
            "services.relay.RelayService.report_context",
            AsyncMock(return_value=context),
        ):
            result = await ReportService().submit(1, chat.session_id, "spam")
        self.assertTrue(result.ok)
        async with self.factory() as s:
            rows = list(
                (
                    await s.scalars(
                        select(ReportSnapshot).where(
                            ReportSnapshot.report_id == result.data["report_id"]
                        )
                    )
                ).all()
            )
            report = await s.get(Report, result.data["report_id"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(
            any(row.kind == "photo" and row.file_id == "AgAC-photo" for row in rows)
        )
        self.assertIn("[фото]", report.message_content)
        self.assertTrue(all(row.expires_at > utcnow() for row in rows))

    async def test_cleanup_drops_expired_media_and_old_text(self):
        chat = await self.repo.match(1, 2, self.redis)
        with patch(
            "services.relay.RelayService.report_context",
            AsyncMock(
                return_value=[
                    {"user_id": 2, "kind": "photo", "file_id": "AgAC-photo", "text": ""}
                ]
            ),
        ):
            result = await ReportService().submit(1, chat.session_id, "spam")
        report_id = result.data["report_id"]
        async with self.factory.begin() as s:
            row = await s.scalar(
                select(ReportSnapshot).where(ReportSnapshot.report_id == report_id)
            )
            row.expires_at = utcnow() - timedelta(seconds=1)
            report = await s.get(Report, report_id)
            report.created_at = utcnow() - timedelta(days=100)
        await cleanup_reports()
        async with self.factory() as s:
            self.assertIsNone(
                await s.scalar(
                    select(ReportSnapshot).where(ReportSnapshot.report_id == report_id)
                )
            )
            self.assertIsNone((await s.get(Report, report_id)).message_content)


class FriendTests(CommunityCase):
    async def test_request_accept_and_list(self):
        chat = await self.ended_chat()
        sent = await FriendService().request(1, chat.session_id)
        self.assertTrue(sent.ok)
        self.assertEqual(sent.code, "SENT")
        accepted = await FriendService().respond(2, sent.data["friendship_id"], True)
        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.code, "ACCEPTED")
        friends = await FriendService().list(1)
        self.assertEqual(friends, [(sent.data["friendship_id"], "#test2")])
        again = await FriendService().request(1, chat.session_id)
        self.assertEqual(again.code, "ALREADY_FRIENDS")

    async def test_reciprocal_request_accepts_immediately(self):
        chat = await self.ended_chat(1, 3)
        first = await FriendService().request(3, chat.session_id)
        self.assertEqual(first.code, "SENT")
        second = await FriendService().request(1, chat.session_id)
        self.assertTrue(second.ok)
        self.assertEqual(second.code, "ACCEPTED")
        async with self.factory() as s:
            row = await s.get(Friendship, first.data["friendship_id"])
        self.assertEqual(row.status, "accepted")

    async def test_block_closes_a_friendship(self):
        chat = await self.ended_chat()
        sent = await FriendService().request(1, chat.session_id)
        await FriendService().respond(2, sent.data["friendship_id"], True)
        second = await self.repo.match(1, 2, self.redis)
        report = await ReportService().submit(1, second.session_id, "insult")
        self.assertTrue(report.ok)
        async with self.factory() as s:
            row = await s.get(Friendship, sent.data["friendship_id"])
        self.assertEqual(row.status, "blocked")

    async def test_stale_pending_request_can_be_replaced(self):
        chat = await self.ended_chat(1, 3)
        first = await FriendService().request(1, chat.session_id)
        self.assertEqual(first.code, "SENT")
        async with self.factory.begin() as s:
            row = await s.get(Friendship, first.data["friendship_id"])
            row.updated_at = utcnow() - timedelta(days=8)
        second = await FriendService().request(1, chat.session_id)
        self.assertTrue(second.ok)
        self.assertEqual(second.code, "SENT")
        self.assertEqual(second.data["friendship_id"], first.data["friendship_id"])

    async def test_friend_invitation_creates_a_direct_chat(self):
        chat = await self.ended_chat()
        sent = await FriendService().request(1, chat.session_id)
        await FriendService().respond(2, sent.data["friendship_id"], True)
        invited = await FriendService().invite(1, sent.data["friendship_id"])
        self.assertTrue(invited.ok)
        joined = await FriendService().join(2, sent.data["friendship_id"])
        self.assertTrue(joined.ok)
        active = await self.repo.active(2)
        self.assertIsNotNone(active)
        self.assertIn(1, (active.user1_id, active.user2_id))


class ReconnectTests(CommunityCase):
    async def test_both_sides_return_to_the_same_partner(self):
        chat = await self.ended_chat()
        first = await ReconnectService().press(1, chat.session_id)
        self.assertTrue(first.ok)
        self.assertEqual(first.code, "WAITING")
        second = await ReconnectService().press(2, chat.session_id)
        self.assertTrue(second.ok)
        self.assertEqual(second.code, "MATCHED")
        active = await self.repo.active(1)
        self.assertIsNotNone(active)
        self.assertIn(2, (active.user1_id, active.user2_id))

    async def test_expired_offer_does_not_match(self):
        chat = await self.ended_chat()
        async with self.factory.begin() as s:
            row = await s.scalar(
                select(ReconnectOffer).where(
                    ReconnectOffer.session_id == chat.session_id
                )
            )
            row.expires_at = utcnow() - timedelta(seconds=1)
        result = await ReconnectService().press(1, chat.session_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "EXPIRED")

    async def test_report_does_not_create_an_offer(self):
        chat = await self.ended_chat(reason="report")
        async with self.factory() as s:
            row = await s.scalar(
                select(ReconnectOffer).where(
                    ReconnectOffer.session_id == chat.session_id
                )
            )
        self.assertIsNone(row)


class ReferralBypassTests(CommunityCase):
    async def test_three_activated_referrals_grant_seven_days_once(self):
        async with self.factory.begin() as s:
            referrer = await s.scalar(select(User).where(User.telegram_id == 1))
            referrer.bot_id = 501
            for i in range(3):
                s.add(Referral(referrer_id=1, referred_id=10 + i, status="activated"))
        async with self.factory.begin() as s:
            referrer = await s.scalar(select(User).where(User.telegram_id == 1))
            await grant_op_bypass(s, referrer)
        async with self.factory() as s:
            rows = list(
                (
                    await s.scalars(select(OpExemption).where(OpExemption.user_id == 1))
                ).all()
            )
            notes = list(
                (
                    await s.scalars(
                        select(Notification).where(
                            Notification.user_id == 1, Notification.type == "op_bypass"
                        )
                    )
                ).all()
            )
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0].until, utcnow() + timedelta(days=6))
        self.assertEqual(len(notes), 1)
        async with self.factory.begin() as s:
            referrer = await s.scalar(select(User).where(User.telegram_id == 1))
            await grant_op_bypass(s, referrer)
        async with self.factory() as s:
            self.assertEqual(
                await s.scalar(
                    select(func.count(OpExemption.id)).where(OpExemption.user_id == 1)
                ),
                1,
            )

    async def test_below_threshold_grants_nothing(self):
        async with self.factory.begin() as s:
            referrer = await s.scalar(select(User).where(User.telegram_id == 3))
            referrer.bot_id = 501
            for i in range(2):
                s.add(Referral(referrer_id=3, referred_id=20 + i, status="activated"))
        async with self.factory.begin() as s:
            referrer = await s.scalar(select(User).where(User.telegram_id == 3))
            self.assertIsNone(await grant_op_bypass(s, referrer))
        async with self.factory() as s:
            self.assertEqual(
                await s.scalar(
                    select(func.count(OpExemption.id)).where(OpExemption.user_id == 3)
                ),
                0,
            )


class RelayAudioTests(unittest.TestCase):
    def test_audio_is_supported(self):
        self.assertIn("audio", RelayService.SUPPORTED)
        self.assertNotIn("document", RelayService.SUPPORTED)
