"""Анти-повтор подбора: недавние собеседники уступают свежим.

Repository-level checks on DatabaseCase: whoever chatted with the seeker
inside MATCH_COOLDOWN_MINUTES loses the queue to fresh candidates, the
fallback allows only the freshest repeat when the page runs dry, and a
block still beats the fallback.
"""

import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

import config
from models.chat import Block, Chat
from models.user import User
from test_chat_duration import DatabaseCase
from utils.time import utcnow


def ended_chat(u1, u2, minutes_ago):
    started = utcnow() - timedelta(minutes=minutes_ago)
    return Chat(
        user1_id=u1,
        user2_id=u2,
        status="ended",
        session_id=uuid.uuid4().hex,
        started_at=started,
        ended_at=started,
        duration_seconds=0,
    )


class MatchCooldownTests(DatabaseCase):
    async def add_rows(self, *rows):
        async with self.factory.begin() as s:
            for row in rows:
                s.add(row)

    async def candidates(self, uid, ids):
        return await self.repo.compatible_candidates(uid, ids)

    async def test_recent_partner_yields_to_fresh_candidate(self):
        # 1 chatted with 2 five minutes ago; 3 is unseen -- no matter the
        # redis score order, the fresh face wins.
        await self.add_rows(ended_chat(1, 2, minutes_ago=5))
        self.assertEqual(await self.candidates(1, [2, 3]), [3])
        self.assertEqual(await self.candidates(1, [3, 2]), [3])

    async def test_only_recent_partner_falls_back_to_freshest(self):
        # Both partners repeat -- the fallback matches the freshest one
        # (2 minutes stale beats 20), so a narrow pool still moves.
        await self.add_rows(
            ended_chat(1, 2, minutes_ago=20),
            ended_chat(1, 3, minutes_ago=2),
        )
        self.assertEqual(await self.candidates(1, [2, 3]), [3])

    async def test_partner_older_than_window_counts_as_fresh(self):
        # 45 minutes ago is past the 30-minute cooldown: no penalty.
        await self.add_rows(ended_chat(1, 2, minutes_ago=45))
        self.assertEqual(await self.candidates(1, [2]), [2])

    async def test_recency_is_symmetric(self):
        # The chat row cites 4 as user1 and 1 as user2 -- the seeker must
        # still see 4 as a repeat.
        u4 = User(
            telegram_id=4,
            anon_id="#test4",
            gender="female",
            age=24,
            onboarding_completed=True,
            onboarding_step="MENU",
            rules_version=1,
            rules_accepted_at=utcnow(),
        )
        await self.add_rows(u4, ended_chat(4, 1, minutes_ago=3))
        self.assertEqual(await self.candidates(1, [4]), [4])
        async with self.factory() as s:
            recent = await self.repo.recent_partners(s, 1, [2, 3, 4])
        self.assertEqual(set(recent), {4})

    async def test_block_beats_the_fallback(self):
        # 2 was blocked right after the chat: soft list is empty, and the
        # fallback must not resurrect a blocked face.
        await self.add_rows(
            ended_chat(1, 2, minutes_ago=2),
            Block(blocker_id=1, blocked_id=2),
        )
        self.assertEqual(await self.candidates(1, [2]), [])

    async def test_cooldown_zero_restores_old_ordering(self):
        with patch.object(config, "MATCH_COOLDOWN_MINUTES", 0):
            await self.add_rows(ended_chat(1, 2, minutes_ago=5))
            self.assertEqual(await self.candidates(1, [2, 3]), [2, 3])
            async with self.factory() as s:
                self.assertEqual(await self.repo.recent_partners(s, 1, [2, 3]), {})

    async def test_no_chats_no_sql_penalty(self):
        # Baseline guard: an empty history keeps every candidate.
        self.assertEqual(await self.candidates(1, [3, 2]), [3, 2])

    async def test_users_missing_from_snapshot_are_skipped(self):
        # Candidate 7 has no user row -- filtered before the cooldown query.
        self.assertEqual(await self.candidates(1, [3, 7]), [3])


if __name__ == "__main__":
    unittest.main()
