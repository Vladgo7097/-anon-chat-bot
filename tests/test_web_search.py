"""web/ws.py search+chat loop checks, same style as test_search_recovery.py:
no real Redis/DB, MatchingService and chat_manager mocked out."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
config.BOT_TOKEN = config.BOT_TOKEN or "123456:TEST-TOKEN-not-real"
config.WEB_SESSION_SECRET = config.WEB_SESSION_SECRET or "test-secret-not-real"

import asyncio
from services.result import ServiceResult
from web import ws


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class RunSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = SimpleNamespace(
            enqueue=AsyncMock(return_value=ServiceResult(True, "SEARCH_STARTED")),
            cancel=AsyncMock(), find=AsyncMock(return_value=None))
        for target, value in (
            ("web.ws.MatchingService", lambda: self.service),
            ("web.ws.get_online_count", AsyncMock(return_value=7)),
            ("services.settings.bot_value", AsyncMock(return_value=60)),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    async def test_match_found_switches_to_chatting(self):
        chat = SimpleNamespace(session_id="sess-1", user1_id=1, user2_id=2)
        self.service.find = AsyncMock(return_value=chat)
        websocket = FakeWebSocket()
        state = {"mode": "searching", "session_id": None}
        await ws._run_search(websocket, 1, state, asyncio.Event())
        self.assertEqual(state["mode"], "chatting")
        self.assertEqual(state["session_id"], "sess-1")
        self.assertEqual(websocket.sent[-1], {"type": "matched", "session_id": "sess-1"})

    async def test_rejected_enqueue_reports_error_and_stays_idle(self):
        self.service.enqueue = AsyncMock(return_value=ServiceResult(False, "BANNED"))
        websocket = FakeWebSocket()
        state = {"mode": "searching", "session_id": None}
        await ws._run_search(websocket, 1, state, asyncio.Event())
        self.assertEqual(state["mode"], "idle")
        self.service.find.assert_not_awaited()
        self.assertEqual(websocket.sent, [{"type": "search_error", "code": "BANNED"}])

    async def test_timeout_cancels_and_reports(self):
        with patch("services.settings.bot_value", AsyncMock(return_value=0)):
            websocket = FakeWebSocket()
            state = {"mode": "searching", "session_id": None}
            await ws._run_search(websocket, 1, state, asyncio.Event())
        self.assertEqual(state["mode"], "idle")
        self.service.cancel.assert_awaited_once_with(1)
        self.assertEqual(websocket.sent, [{"type": "search_timeout"}])


class RunChatTests(unittest.IsolatedAsyncioTestCase):
    def make_redis(self, get_message_results):
        pubsub = SimpleNamespace(
            subscribe=AsyncMock(), unsubscribe=AsyncMock(), close=AsyncMock(),
            get_message=AsyncMock(side_effect=get_message_results),
        )
        redis = SimpleNamespace(pubsub=lambda: pubsub)
        return redis, pubsub

    async def test_relayed_message_is_forwarded_to_the_socket(self):
        stop = asyncio.Event()

        async def one_then_stop(*a, **kw):
            stop.set()
            return {"type": "message", "data": json.dumps(
                {"type": "message", "from": 2, "content_type": "text", "text": "hi"})}

        redis, pubsub = self.make_redis(one_then_stop)
        with patch("web.ws.get_redis", AsyncMock(return_value=redis)):
            websocket = FakeWebSocket()
            state = {"mode": "chatting", "session_id": "sess-1"}
            await ws._run_chat(websocket, 1, state, stop)
        pubsub.subscribe.assert_awaited_once_with("chat:sess-1")
        pubsub.unsubscribe.assert_awaited_once_with("chat:sess-1")
        self.assertEqual(websocket.sent, [{"type": "message", "from": 2, "content_type": "text", "text": "hi"}])

    async def test_chat_ended_event_flips_state_to_idle(self):
        stop = asyncio.Event()

        async def one_then_stop(*a, **kw):
            stop.set()
            return {"type": "message", "data": json.dumps({"type": "chat_ended", "reason": "stop", "by": 2})}

        redis, _ = self.make_redis(one_then_stop)
        with patch("web.ws.get_redis", AsyncMock(return_value=redis)):
            websocket = FakeWebSocket()
            state = {"mode": "chatting", "session_id": "sess-1"}
            await ws._run_chat(websocket, 1, state, stop)
        self.assertEqual(state["mode"], "idle")
        self.assertIsNone(state["session_id"])


if __name__ == "__main__":
    unittest.main()
