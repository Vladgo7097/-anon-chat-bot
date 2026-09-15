import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.onboarding import OnboardingService, step_for
from keyboards.reply import onboarding_kb, main_menu_kb


class MemoryUsers:
    def __init__(self):
        self.user = SimpleNamespace(gender=None, age=None, onboarding_step="WELCOME",
            onboarding_completed=False, rules_version=None, rules_accepted_at=None,
            is_banned=False, ban_expires=None, referrer_id=None, deleted_at=None, language="ru")
        self.lock = asyncio.Lock()

    async def get_or_create(self, *args):
        return self.user, False

    async def transition(self, uid, operation):
        async with self.lock:
            return await operation(self.user, SimpleNamespace(scalar=AsyncMock(return_value=None)))


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_brand_new_user_picks_a_language_before_anything_else(self):
        repo = MemoryUsers()
        repo.user.language = None
        service = OnboardingService(repo)
        self.assertEqual((await service.start(1)).code, "LANGUAGE")
        self.assertEqual((await service.advance(1, "begin")).code, "LANGUAGE")
        self.assertEqual((await service.advance(1, "lang", "uk")).code, "WELCOME")
        self.assertEqual(repo.user.language, "uk")

    async def test_an_already_onboarded_user_with_no_language_skips_the_step(self):
        # language is NULL for every account onboarded before this feature
        # shipped -- step_for() must not knock a finished account back into
        # onboarding just because that column happens to be unset.
        repo = MemoryUsers()
        repo.user.language = None
        repo.user.gender, repo.user.age = "male", 23
        repo.user.onboarding_completed = True
        repo.user.rules_version = 1
        from utils.time import utcnow
        repo.user.rules_accepted_at = utcnow()
        self.assertEqual(step_for(repo.user), "MENU")

    async def test_gender_tap_works_straight_from_the_welcome_screen(self):
        # WELCOME and GENDER are now shown as one combined screen (a gender
        # tap right on the welcome message) -- no separate "begin" step
        # should be required to reach AGE.
        repo = MemoryUsers()
        service = OnboardingService(repo)
        self.assertEqual((await service.start(1)).code, "WELCOME")
        self.assertEqual((await service.advance(1, "gender", "female")).code, "AGE")
        self.assertEqual(repo.user.gender, "female")

    async def test_complete_and_resume_after_restart(self):
        repo = MemoryUsers()
        service = OnboardingService(repo)
        self.assertEqual((await service.start(1)).code, "WELCOME")
        self.assertEqual((await service.advance(1, "begin")).code, "GENDER")
        self.assertEqual((await service.advance(1, "gender", "unknown")).code, "GENDER")
        self.assertEqual((await service.advance(1, "gender", "female")).code, "AGE")
        service = OnboardingService(repo)
        self.assertEqual((await service.start(1)).code, "AGE")
        for age in (17, 0, 100, "➡️ Дальше"):
            self.assertFalse((await service.advance(1, "age", age)).ok)
            self.assertIsNone(repo.user.age)
        self.assertEqual((await service.advance(1, "age", 23)).code, "RULES")
        self.assertEqual((await service.advance(1, "accept")).code, "MENU")
        accepted = repo.user.rules_accepted_at
        await service.advance(1, "accept")
        self.assertEqual(repo.user.rules_accepted_at, accepted)
        self.assertIsNotNone(accepted.tzinfo)
        self.assertEqual((await service.start(1)).code, "MENU")

    async def test_advance_clears_a_soft_deleted_account_reentering_via_buttons(self):
        # ProfileService.delete() sets deleted_at and wipes gender/age/step to
        # WELCOME. A user who then taps buttons (not /start) goes through
        # advance(), not start() -- deleted_at must not survive re-onboarding.
        repo = MemoryUsers()
        repo.user.deleted_at = "2026-01-01T00:00:00+00:00"
        service = OnboardingService(repo)
        self.assertEqual((await service.advance(1, "begin")).code, "GENDER")
        self.assertIsNone(repo.user.deleted_at)
        await service.advance(1, "gender", "male")
        await service.advance(1, "age", 23)
        self.assertEqual((await service.advance(1, "accept")).code, "MENU")
        self.assertIsNone(repo.user.deleted_at)

    async def test_cannot_accept_rules_before_age(self):
        repo = MemoryUsers()
        await OnboardingService(repo).advance(1, "accept")
        self.assertFalse(repo.user.onboarding_completed)
        repo.user.onboarding_completed = True
        self.assertNotEqual(step_for(repo.user), "MENU")

    def test_age_pages_and_blue_menu(self):
        ages = {int(b.text) for page in range(7) for row in onboarding_kb("AGE", page).keyboard
                for b in row if b.text.isdigit()}
        self.assertEqual(ages, set(range(18, 100)))
        menu = main_menu_kb()
        self.assertTrue(menu.is_persistent)
        # icon_custom_emoji_id + style silently drop the button's text on
        # Telegram Web (confirmed live) -- reply buttons must stay plain text
        # with no icon, unlike inline buttons where both render fine.
        self.assertTrue(all(b.icon_custom_emoji_id is None and b.style is None
                             for row in menu.keyboard for b in row))
        self.assertTrue(all(b.text for row in menu.keyboard for b in row))


if __name__ == "__main__":
    unittest.main()
