"""Call lifecycle for 1-on-1 calls between two people already in a chat.

Live state lives in Redis (a call is minutes long and does not need to survive
a restart); PostgreSQL keeps only the record that a call happened, for stats
and abuse handling. Media never touches this code -- it goes browser to
browser through TURN. Here we only decide who is allowed to call whom, and
tell both sides what happened.

Deliberately unlimited: no cap on call duration and no cap on how many calls
run at once. The only real ceiling is the server's uplink, and that belongs in
monitoring, not in an arbitrary constant.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from sqlalchemy import select

import config
from models.base import async_session
from models.chat import Call
from services.result import ServiceResult
from utils.time import utcnow


# One wording for the offer, used by both surfaces so the promise made to the
# person is identical whether it arrives in Telegram or on the site.
OFFER_TEXT = (
    "🎙 Собеседник предлагает созвониться.\n\n"
    "Звонок откроется по ссылке в браузере. Имя, номер и username не передаются — "
    "только голос. Камера выключена, включить можно вручную с обеих сторон."
)


def _state_key(session_id):
    return f"call:{session_id}"


def _token_key(token):
    return f"call:token:{token}"


async def _redis():
    from services.chat_manager import get_redis
    return await get_redis()


def ice_servers(user_id):
    """Short-lived TURN credentials (the standard HMAC scheme coturn calls
    use-auth-secret). They expire on their own, so a leaked page cannot be
    turned into a free relay."""
    if not config.TURN_HOST or not config.TURN_SECRET:
        return []
    expiry = int(time.time()) + config.TURN_CREDENTIAL_TTL
    username = f"{expiry}:{user_id}"
    digest = hmac.new(config.TURN_SECRET.encode(), username.encode(), hashlib.sha1).digest()
    credential = base64.b64encode(digest).decode()
    return [{
        "urls": [
            f"turn:{config.TURN_HOST}:3478?transport=udp",
            f"turn:{config.TURN_HOST}:3478?transport=tcp",
            f"turns:{config.TURN_HOST}:5349?transport=tcp",
        ],
        "username": username,
        "credential": credential,
    }]


class CallService:
    async def state(self, session_id):
        raw = await (await _redis()).get(_state_key(session_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def offer(self, user_id):
        """Premium initiates; the other side may be anyone. Requires an active
        chat and no call already running in it."""
        from services.chats import ChatService
        from utils.db import is_premium
        if not await is_premium(user_id):
            return ServiceResult(False, "PREMIUM_REQUIRED")
        chat = await ChatService().active(user_id)
        if not chat:
            return ServiceResult(False, "NOT_CHATTING")
        if await self.state(chat.session_id):
            return ServiceResult(False, "CALL_IN_PROGRESS")
        partner = chat.user2_id if chat.user1_id == user_id else chat.user1_id
        async with async_session.begin() as s:
            call = Call(chat_session_id=chat.session_id, initiator_id=user_id,
                        partner_id=partner, status="offered")
            s.add(call)
            await s.flush()
            call_id = call.id
        state = {"status": "offered", "initiator": user_id, "partner": partner,
                 "call_id": call_id, "session_id": chat.session_id}
        # The offer expires on its own; a missed call must not wedge the chat.
        await (await _redis()).set(_state_key(chat.session_id), json.dumps(state),
                                   ex=config.CALL_OFFER_TTL)
        return ServiceResult(True, "OFFERED", {"session_id": chat.session_id,
                                               "partner": partner, "call_id": call_id})

    async def accept(self, user_id, session_id):
        state = await self.state(session_id)
        if not state or state["status"] != "offered":
            return ServiceResult(False, "NO_OFFER")
        if user_id != state["partner"]:
            return ServiceResult(False, "NOT_YOURS")
        state["status"] = "active"
        state["started_at"] = time.time()
        # No expiry from here on: a call may last as long as the two want.
        await (await _redis()).set(_state_key(session_id), json.dumps(state))
        async with async_session.begin() as s:
            call = await s.get(Call, state["call_id"])
            if call:
                call.status, call.started_at = "active", utcnow()
        return ServiceResult(True, "ACCEPTED", {"state": state})

    async def decline(self, user_id, session_id):
        state = await self.state(session_id)
        if not state or state["status"] != "offered":
            return ServiceResult(False, "NO_OFFER")
        if user_id != state["partner"]:
            return ServiceResult(False, "NOT_YOURS")
        await self._close(state, "declined")
        return ServiceResult(True, "DECLINED", {"state": state})

    async def hangup(self, user_id, session_id, reason="hangup"):
        state = await self.state(session_id)
        if not state:
            return ServiceResult(False, "NO_CALL")
        if user_id not in (state["initiator"], state["partner"]):
            return ServiceResult(False, "NOT_YOURS")
        await self._close(state, reason)
        return ServiceResult(True, "ENDED", {"state": state})

    async def end_for_session(self, session_id, reason="chat_ended"):
        """Called when the chat itself ends -- a call cannot outlive it."""
        state = await self.state(session_id)
        if state:
            await self._close(state, reason)
        return state

    async def _close(self, state, reason):
        await (await _redis()).delete(_state_key(state["session_id"]))
        async with async_session.begin() as s:
            call = await s.get(Call, state["call_id"])
            if call and not call.ended_at:
                now = utcnow()
                call.status = "ended" if state["status"] == "active" else reason
                call.ended_at, call.end_reason = now, reason
                if call.started_at:
                    call.duration_seconds = max(0, int((now - call.started_at).total_seconds()))

    async def mint_room_token(self, user_id, session_id):
        """One-time ticket into the call room. Someone accepting from the bot
        may have no website session at all, and a call cannot wait for a login,
        so the link itself carries the authorisation -- once, briefly."""
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"telegram_id": user_id, "session_id": session_id})
        await (await _redis()).set(_token_key(token), payload, ex=config.CALL_TOKEN_TTL)
        return token

    async def redeem_room_token(self, token):
        r = await _redis()
        key = _token_key(token)
        raw = await r.get(key)
        if not raw:
            return None
        # Single use: burn it before returning, so a shared link is worthless.
        await r.delete(key)
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def history_count(self, user_id):
        async with async_session() as s:
            from sqlalchemy import func
            return await s.scalar(select(func.count(Call.id)).where(
                (Call.initiator_id == user_id) | (Call.partner_id == user_id))) or 0
