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
            is_banned=False, ban_expires=None, referrer_id=None, deleted_at=None)
        self.lock = asyncio.Lock()

    async def get_or_create(self, *args):
        return self.user

    async def transition(self, uid, operation):
        async with self.lock:
            return await operation(self.user, SimpleNamespace(scalar=AsyncMock(return_value=None)))


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertTrue(all(b.style == "primary" for row in menu.keyboard for b in row))


if __name__ == "__main__":
    unittest.main()
