"""/growth -- the acquisition funnel an operator actually reads.

Covers the three things that can silently rot: return/chat rates computed
from each broadcast's own send time (a 7-day view must not credit old sends
with new returns), source tags parsed without postgres-only split_part, and
the rolling window -- a campaign shipped an hour ago must be visible in the
default 24-hour view.
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from test_chat_duration import DatabaseCase
from models.user import User
from models.chat import Chat
from models.operations import Notification, Broadcast, AnalyticsEvent
from services.admin import StatsService
from handlers.admin import growth_text, cmd_growth
from utils.time import utcnow


def report(**overrides):
    base = dict(days=1, jobs=[], nudges={}, sources={}, new=0, completed=0, chats=0)
    base.update(overrides)
    return base


class GrowthServiceTests(DatabaseCase):
    async def test_funnel_reads_returns_nudges_and_source_tags(self):
        now = utcnow()
        async with self.factory.begin() as s:
            sent_at = now-timedelta(minutes=10)
            bc = Broadcast(admin_id=99, segment="inactive", content="x",
                           status="sending", sent=2, created_at=sent_at)
            s.add(bc)
            await s.flush()
            for uid in (1, 2):
                s.add(Notification(user_id=uid, type="broadcast",
                                   dedupe_key=f"growth:{bc.id}:{uid}", text="x",
                                   status="sent", marketing=True,
                                   broadcast_id=bc.id, sent_at=sent_at))
            s.add(Notification(user_id=3, type="broadcast",
                               dedupe_key=f"growth:{bc.id}:3", text="x",
                               status="queued", marketing=True, broadcast_id=bc.id))
            s.add(Notification(user_id=1, type="inactive", dedupe_key="growth:in:1",
                               text="x", status="sent", sent_at=now))
            s.add(Notification(user_id=2, type="prime_time", dedupe_key="growth:pt:2",
                               text="x", status="sent", sent_at=now))
            s.add(AnalyticsEvent(user_id=1, event="start_source",
                                 dedupe_key="start_source:1:chat_davinchik",
                                 created_at=now))
            # Recipient 2 came back after the send and started a chat with a
            # non-recipient; recipient 1 never returned.
            s.add(Chat(user1_id=2, user2_id=3, status="active",
                       started_at=now-timedelta(seconds=30)))
            users = {u.telegram_id: u for u in (await s.scalars(select(User))).all()}
            users[1].last_online = now-timedelta(days=10)
            users[2].last_online = now-timedelta(minutes=1)
        g = await StatsService().growth(1)
        self.assertEqual(len(g["jobs"]), 1)
        job = g["jobs"][0]
        self.assertEqual((job["sent"], job["queued"], job["returned"], job["chatted"]),
                         (2, 1, 1, 1))
        self.assertEqual(g["nudges"], {"inactive": 1, "prime_time": 1})
        self.assertEqual(g["sources"], {"chat_davinchik": 1})
        self.assertEqual((g["new"], g["completed"], g["chats"]), (3, 3, 1))

    async def test_window_is_rolling_not_calendar_days(self):
        now = utcnow()
        async with self.factory.begin() as s:
            s.add(Broadcast(admin_id=99, segment="inactive", content="x",
                            status="completed", created_at=now-timedelta(days=2)))
        self.assertEqual((await StatsService().growth(1))["jobs"], [])
        self.assertEqual(len((await StatsService().growth(7))["jobs"]), 1)


class GrowthTextTests(unittest.TestCase):
    def test_empty_period_says_so(self):
        text = growth_text(report(), 1)
        self.assertIn("за 24 ч", text)
        self.assertIn("Рассылок за период не было", text)

    def test_job_line_shows_the_full_funnel(self):
        job = dict(id=4, created_at=datetime(2026, 9, 22, 23, 39), sent=2886,
                   failed=483, queued=318, returned=144, chatted=12)
        text = growth_text(report(jobs=[job],
                                  nudges={"inactive": 5, "prime_time": 2},
                                  sources={"chat_davinchik": 3},
                                  new=10, completed=7, chats=20), 1)
        self.assertIn("#4 от 22.09 23:39 — 2886 доставлено, 483 ошибок, 318 в очереди", text)
        self.assertIn("вернулись 144 (5%), из них в диалоге 12", text)
        self.assertIn("спящим 5, вечерний пик 2", text)
        self.assertIn("chat_davinchik 3", text)
        self.assertIn("Новых 10 (дошли до конца 7) · Диалогов начато 20", text)


class CmdGrowthTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_period_prints_usage_without_querying(self):
        message = MagicMock(text="/growth 5")
        message.answer = AsyncMock()
        with patch("handlers.admin.StatsService") as service:
            await cmd_growth(message)
            service.assert_not_called()
        self.assertIn("/growth [1|7|30]", message.answer.call_args.args[0])

    async def test_default_run_is_the_rolling_day_with_live_online(self):
        message = MagicMock(text="/growth")
        message.answer = AsyncMock()
        with patch("handlers.admin.StatsService") as service, \
                patch("handlers.admin.get_online_count", AsyncMock(return_value=7)):
            service.return_value.growth = AsyncMock(return_value=report(
                nudges={"prime_time": 1}))
            await cmd_growth(message)
            service.return_value.growth.assert_awaited_once_with(1)
        text = message.answer.call_args.args[0]
        self.assertIn("вечерний пик 1", text)
        self.assertIn("Онлайн: 7", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
