"""ProfileService.delete() must never crash on a missing row.

delete() re-fetches the user in a second transaction after the first one
already committed deleted_at; that second .scalar() was used unguarded,
unlike the first fetch in this same function and the sibling marketing()
method right below it. Accounts are never hard-deleted by this app, so the
row can't normally vanish between the two transactions -- but a manual DB
operation between them must fail cleanly, not crash with an AttributeError
on a None user.
"""
import unittest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from test_chat_duration import DatabaseCase
from models.user import User
from repositories.users import UserRepository
from services.profile import ProfileService
from services.result import ServiceResult


class ProfileDeleteTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # delete() also calls MatchingService().cancel(), which touches Redis
        # to drop the search-queue entry -- irrelevant to this fix, stub it out.
        redis = AsyncMock()
        p = patch("services.chat_manager.get_redis", AsyncMock(return_value=redis))
        p.start()
        self.addCleanup(p.stop)

    async def test_a_normal_delete_still_works(self):
        await UserRepository().get_or_create(9001, "u", "N")
        result = await ProfileService().delete(9001)
        self.assertEqual(result, ServiceResult(True, "DELETED"))
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 9001))
            self.assertIsNotNone(user.deleted_at)
            self.assertIsNone(user.username)
            self.assertEqual(user.onboarding_step, "WELCOME")

    async def test_bio_is_wiped_on_delete_like_the_rest_of_the_profile(self):
        await UserRepository().get_or_create(9005, "u", "N")
        await ProfileService().edit(9005, "bio", "Coffee and long walks")
        await ProfileService().delete(9005)
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 9005))
            self.assertIsNone(user.bio)

    async def test_a_row_missing_before_the_second_transaction_is_reported_cleanly(self):
        await UserRepository().get_or_create(9002, "u", "N")

        from unittest.mock import patch
        from sqlalchemy.ext.asyncio import AsyncSession

        original = AsyncSession.scalar
        user_lookups = 0

        async def vanish_on_second_user_lookup(self, *args, **kwargs):
            nonlocal user_lookups
            # delete() also runs ChatService().end_chat(), which does its own
            # unrelated .scalar() against the chats table -- only count
            # lookups against users so that one doesn't throw off which call
            # is delete()'s own second fetch.
            if "FROM users" in str(args[0]):
                user_lookups += 1
                if user_lookups == 2:
                    # Simulate the row having been removed between the two
                    # transactions delete() opens -- never happens through
                    # this app's own code, but must not crash if it ever does.
                    return None
            return await original(self, *args, **kwargs)

        with patch.object(AsyncSession, "scalar", vanish_on_second_user_lookup):
            result = await ProfileService().delete(9002)
        self.assertEqual(result, ServiceResult(False, "NOT_FOUND"))

    async def test_deleting_an_unknown_user_id_does_not_crash(self):
        result = await ProfileService().delete(9003)
        self.assertEqual(result, ServiceResult(False, "NOT_FOUND"))


class ProfileBioTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # edit() also calls MatchingService().cancel(), which touches Redis
        # to drop the search-queue entry -- irrelevant here, stub it out.
        redis = AsyncMock()
        p = patch("services.chat_manager.get_redis", AsyncMock(return_value=redis))
        p.start()
        self.addCleanup(p.stop)

    async def test_bio_can_be_set_and_read_back(self):
        await UserRepository().get_or_create(9010, "u", "N")
        result = await ProfileService().edit(9010, "bio", "Coffee and long walks")
        self.assertEqual(result, ServiceResult(True, "SAVED"))
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 9010))
            self.assertEqual(user.bio, "Coffee and long walks")

    async def test_bio_can_be_cleared_with_an_empty_string(self):
        await UserRepository().get_or_create(9011, "u", "N")
        await ProfileService().edit(9011, "bio", "temporary")
        result = await ProfileService().edit(9011, "bio", "")
        self.assertEqual(result, ServiceResult(True, "SAVED"))
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 9011))
            self.assertEqual(user.bio, "")

    async def test_bio_over_150_characters_is_rejected(self):
        await UserRepository().get_or_create(9012, "u", "N")
        result = await ProfileService().edit(9012, "bio", "x" * 151)
        self.assertEqual(result, ServiceResult(False, "INVALID"))
        async with self.factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 9012))
            self.assertIsNone(user.bio)


if __name__ == "__main__":
    unittest.main(verbosity=2)
