"""Provocative return copy must stay honest and stable.

The reactivation nudge rotates through pools of variants instead of one
flat "we miss you" line. Two invariants are load-bearing:
- hot copy quotes a real chat count, cold copy (count == 0) must never
  claim traffic -- advertising an empty room kills the return;
- rotation is deterministic per user+day: a worker restart must not resend
  a different variant mid-cycle, but consecutive days should differ.
"""
import re
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import Notification
from services.notifications import (INACTIVE_TEXTS_HOT, INACTIVE_TEXTS_COLD,
                                    ONBOARDING_NUDGE_TEXTS, NotificationService,
                                    _rotate)
from utils.time import utcnow

LANGS = ("ru", "en", "uk")


class PoolShapeTests(unittest.TestCase):
    def _check(self, pool, name):
        self.assertTrue(pool, f"{name} pool is empty")
        for index, variant in enumerate(pool):
            for lang in LANGS:
                self.assertIn(lang, variant, f"{name}[{index}] misses {lang}")
                self.assertTrue(variant[lang].strip(), f"{name}[{index}][{lang}] empty")

    def test_every_pool_is_complete_and_translated(self):
        self._check(INACTIVE_TEXTS_HOT, "hot")
        self._check(INACTIVE_TEXTS_COLD, "cold")
        self._check(ONBOARDING_NUDGE_TEXTS, "nudge")

    def test_only_hot_copy_may_quote_a_count(self):
        for variant in INACTIVE_TEXTS_HOT:
            for lang in LANGS:
                self.assertIn("{count}", variant[lang])
        for pool in (INACTIVE_TEXTS_COLD, ONBOARDING_NUDGE_TEXTS):
            for variant in pool:
                for lang in LANGS:
                    self.assertNotIn("{count}", variant[lang])

    def test_rotate_is_deterministic_and_covers_the_pool(self):
        first = _rotate(INACTIVE_TEXTS_HOT, 42, 100)
        self.assertIs(first, _rotate(INACTIVE_TEXTS_HOT, 42, 100))
        seen = {id(_rotate(INACTIVE_TEXTS_HOT, uid, day))
                for uid in range(500) for day in (100, 101)}
        self.assertEqual(len(seen), len(INACTIVE_TEXTS_HOT),
                         "some variants are unreachable")

    def test_a_new_day_can_change_the_message_for_the_same_user(self):
        changed = any(_rotate(INACTIVE_TEXTS_HOT, uid, 100) is not _rotate(INACTIVE_TEXTS_HOT, uid, 101)
                      for uid in range(50))
        self.assertTrue(changed, "rotation ignores the day salt")


def _cold_texts():
    return {variant[lang] for variant in INACTIVE_TEXTS_COLD for lang in LANGS}


def _hot_patterns():
    return [re.compile(re.escape(variant[lang]).replace(re.escape("{count}"), r"\d+"))
            for variant in INACTIVE_TEXTS_HOT for lang in LANGS]


class ProvocationScheduleTests(DatabaseCase):
    async def test_schedule_sends_pool_copy_not_a_flat_line(self):
        async with self.factory.begin() as s:
            for user in (await s.scalars(select(User))).all():
                user.last_online = utcnow()-timedelta(days=4)
        await NotificationService().schedule()
        async with self.factory() as s:
            notices = (await s.scalars(select(Notification).where(
                Notification.type == "inactive"))).all()
            self.assertTrue(notices, "no return nudge was scheduled")
            hot = _hot_patterns()
            for notice in notices:
                self.assertTrue(
                    notice.text in _cold_texts()
                    or any(pattern.fullmatch(notice.text) for pattern in hot),
                    f"text outside the provocation pools: {notice.text!r}")
                self.assertEqual(notice.callback_data, "search:start")

    async def test_a_dropout_gets_rotating_nudge_copy(self):
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 1))
            user.onboarding_completed = False
            user.created_at = utcnow()-timedelta(days=1)
            user.language = "ru"
        await NotificationService().schedule()
        async with self.factory() as s:
            nudges = (await s.scalars(select(Notification).where(
                Notification.type == "onboarding_nudge"))).all()
            self.assertEqual(len(nudges), 1)
            allowed = {variant["ru"] for variant in ONBOARDING_NUDGE_TEXTS}
            self.assertIn(nudges[0].text, allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
