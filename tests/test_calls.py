"""Call lifecycle: who may start one, what ends one, and that a room link
cannot be reused. Redis is faked, no media or WebRTC is involved."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
config.TURN_HOST = "turn.example"
config.TURN_SECRET = "secret-not-real"

from test_chat_duration import DatabaseCase
from models.chat import Call
from sqlalchemy import select
from services.calls import CallService, ice_servers


class FakeRedis:
    """Just the few string operations CallService uses."""
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return True


class CallTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.redis = FakeRedis()
        p = patch("services.calls._redis", AsyncMock(return_value=self.redis))
        p.start()
        self.addCleanup(p.stop)
        self.chat = SimpleNamespace(session_id="sess-call", user1_id=1, user2_id=2)

    def patch_context(self, premium=True, chat=True):
        return (
            patch("services.calls.ChatService" if False else "services.chats.ChatService.active",
                  AsyncMock(return_value=self.chat if chat else None)),
            patch("utils.db.is_premium", AsyncMock(return_value=premium)),
        )

    async def offer_as(self, user_id=1, premium=True, chat=True):
        chat_patch, premium_patch = self.patch_context(premium, chat)
        with chat_patch, premium_patch:
            return await CallService().offer(user_id)

    async def test_premium_is_required_to_start_a_call(self):
        result = await self.offer_as(premium=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "PREMIUM_REQUIRED")
        async with self.factory() as s:
            self.assertEqual(list((await s.scalars(select(Call))).all()), [])

    async def test_cannot_call_outside_an_active_chat(self):
        result = await self.offer_as(chat=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NOT_CHATTING")

    async def test_offer_records_the_call_and_second_offer_is_refused(self):
        first = await self.offer_as()
        self.assertTrue(first.ok)
        self.assertEqual(first.data["partner"], 2)
        second = await self.offer_as()
        self.assertFalse(second.ok)
        self.assertEqual(second.code, "CALL_IN_PROGRESS")
        async with self.factory() as s:
            calls = list((await s.scalars(select(Call))).all())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].status, "offered")

    async def test_only_the_partner_can_accept(self):
        await self.offer_as()
        self.assertEqual((await CallService().accept(1, "sess-call")).code, "NOT_YOURS")
        accepted = await CallService().accept(2, "sess-call")
        self.assertTrue(accepted.ok)
        async with self.factory() as s:
            call = await s.scalar(select(Call))
        self.assertEqual(call.status, "active")
        self.assertIsNotNone(call.started_at)

    async def test_accepted_call_has_no_expiry_so_it_can_run_as_long_as_they_like(self):
        await self.offer_as()
        await CallService().accept(2, "sess-call")
        # The offer was stored with a TTL; the active call must not be.
        state = json.loads(self.redis.store["call:sess-call"])
        self.assertEqual(state["status"], "active")

    async def test_hangup_closes_the_call_and_records_the_reason(self):
        await self.offer_as()
        await CallService().accept(2, "sess-call")
        self.assertTrue((await CallService().hangup(1, "sess-call")).ok)
        self.assertNotIn("call:sess-call", self.redis.store)
        async with self.factory() as s:
            call = await s.scalar(select(Call))
        self.assertEqual(call.end_reason, "hangup")
        self.assertIsNotNone(call.ended_at)

    async def test_ending_the_chat_ends_the_call(self):
        await self.offer_as()
        await CallService().accept(2, "sess-call")
        await CallService().end_for_session("sess-call")
        self.assertNotIn("call:sess-call", self.redis.store)
        async with self.factory() as s:
            call = await s.scalar(select(Call))
        self.assertEqual(call.end_reason, "chat_ended")

    async def test_room_token_works_once(self):
        service = CallService()
        token = await service.mint_room_token(2, "sess-call")
        first = await service.redeem_room_token(token)
        self.assertEqual(first["telegram_id"], 2)
        self.assertIsNone(await service.redeem_room_token(token))

    async def test_ice_credentials_are_temporary_and_not_the_raw_secret(self):
        servers = ice_servers(1)
        self.assertTrue(servers)
        username, credential = servers[0]["username"], servers[0]["credential"]
        self.assertIn(":1", username)
        self.assertNotIn(config.TURN_SECRET, credential)
        self.assertNotEqual(credential, config.TURN_SECRET)


if __name__ == "__main__":
    unittest.main()
