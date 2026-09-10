"""Where a new signup's /start payload goes: it used to be read and discarded.

Only the "ref_" program's own code was ever kept, so there was no way to tell
apart a low-intent visitor from a bot catalog and a genuinely broken first
screen -- every other deep-link source vanished the instant it arrived.
"""
import unittest
from sqlalchemy import select, func

from test_chat_duration import DatabaseCase
from models.operations import AnalyticsEvent
from services.onboarding import OnboardingService


class StartSourceTests(DatabaseCase):
    async def sources(self, telegram_id=None):
        async with self.factory() as s:
            query = select(AnalyticsEvent).where(AnalyticsEvent.event == "start_source")
            if telegram_id is not None:
                query = query.where(AnalyticsEvent.user_id == telegram_id)
            return (await s.scalars(query)).all()

    async def test_a_non_ref_payload_is_recorded_instead_of_discarded(self):
        await OnboardingService().start(9001, "u", "N", payload="catalog123")
        rows = await self.sources(9001)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].dedupe_key, "start_source:9001:catalog123")

    async def test_a_bare_start_is_recorded_as_none_not_dropped_silently(self):
        await OnboardingService().start(9002, "u", "N", payload="")
        rows = await self.sources(9002)
        self.assertEqual([r.dedupe_key for r in rows], ["start_source:9002:none"])

    async def test_it_is_recorded_once_even_across_repeated_start_commands(self):
        for _ in range(3):
            await OnboardingService().start(9003, "u", "N", payload="promo_x")
        self.assertEqual(len(await self.sources(9003)), 1)

    async def test_a_returning_user_pressing_start_again_is_not_recorded_again(self):
        await OnboardingService().start(9004, "u", "N", payload="first_link")
        await OnboardingService().start(9004, "u", "N", payload="second_link")
        rows = await self.sources(9004)
        # The source that actually brought them in is what matters; a later
        # /start with a different link must not look like a second signup.
        self.assertEqual([r.dedupe_key for r in rows], ["start_source:9004:first_link"])

    async def test_stray_characters_are_stripped_and_the_value_is_capped(self):
        await OnboardingService().start(9005, "u", "N", payload="a!b/c "+"x"*100)
        rows = await self.sources(9005)
        self.assertEqual(len(rows), 1)
        value = rows[0].dedupe_key.split(":", 2)[2]
        self.assertRegex(value, r"^[A-Za-z0-9_-]{1,48}$")

    async def test_the_ref_program_still_works_alongside_the_new_logging(self):
        referrer, _ = await self._referrer()
        await OnboardingService().start(9006, "u", "N", payload=f"ref_{referrer.referral_code}")
        async with self.factory() as s:
            from models.user import User
            user = await s.scalar(select(User).where(User.telegram_id == 9006))
            self.assertEqual(user.referrer_id, referrer.telegram_id)
        rows = await self.sources(9006)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].dedupe_key.startswith("start_source:9006:ref_"))

    async def _referrer(self):
        from repositories.users import UserRepository
        return await UserRepository().get_or_create(9100, "referrer", "R")


if __name__ == "__main__":
    unittest.main(verbosity=2)
