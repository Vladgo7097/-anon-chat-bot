"""HTTP surface for calls: offering, answering, hanging up, TURN credentials
and the one-time room link.

Signalling itself (SDP/ICE) does not pass through here -- it rides the
existing WebSocket in web/ws.py. These endpoints only move a call between
states and tell the other side, whichever surface they are on.
"""
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

import config
from web.auth import SESSION_COOKIE, create_session_token, current_telegram_id
from web.routes_chat import outbound_bot

router = APIRouter()

CALL_ERRORS = {
    "PREMIUM_REQUIRED": 402,
    "NOT_CHATTING": 409,
    "CALL_IN_PROGRESS": 409,
    "NO_OFFER": 409,
    "NO_CALL": 409,
    "NOT_YOURS": 403,
}


def _fail(result):
    raise HTTPException(status_code=CALL_ERRORS.get(result.code, 400), detail=result.code)


async def _publish(session_id, payload):
    from services.relay import RelayService
    await RelayService().publish_delivery(session_id, payload)


@router.post("/api/call/offer")
async def offer_call(telegram_id: int = Depends(current_telegram_id)):
    from services.calls import CallService
    result = await CallService().offer(telegram_id)
    if not result.ok:
        _fail(result)
    session_id, partner = result.data["session_id"], result.data["partner"]
    # Tell a website peer immediately...
    await _publish(session_id, {"type": "call_offer", "from": telegram_id, "session_id": session_id})
    # ...and the Telegram side too, since they may not have the site open.
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Принять звонок", callback_data=f"v1:call:yes:{session_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"v1:call:no:{session_id}"),
    ]])
    from services.calls import OFFER_TEXT
    try:
        await outbound_bot().send_message(partner, OFFER_TEXT, reply_markup=keyboard)
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    return {"ok": True, "session_id": session_id}


@router.post("/api/call/accept")
async def accept_call(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    from services.calls import CallService
    service = CallService()
    session_id = payload.get("session_id")
    result = await service.accept(telegram_id, session_id)
    if not result.ok:
        _fail(result)
    state = result.data["state"]
    await _publish(session_id, {"type": "call_accepted", "by": telegram_id, "session_id": session_id})
    # The initiator may be sitting in Telegram rather than on the site, and the
    # pub/sub event alone would never reach them. A one-time link always does.
    token = await service.mint_room_token(state["initiator"], session_id)
    try:
        await outbound_bot().send_message(
            state["initiator"], "✅ Собеседник принял звонок. Открой комнату:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="Войти в звонок", url=f"https://{config.TURN_HOST}/call/{token}")]]))
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    # The initiator is the polite peer here: they create the WebRTC offer.
    return {"ok": True, "session_id": session_id, "initiator": state["initiator"]}


@router.post("/api/call/decline")
async def decline_call(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    from services.calls import CallService
    session_id = payload.get("session_id")
    result = await CallService().decline(telegram_id, session_id)
    if not result.ok:
        _fail(result)
    await _publish(session_id, {"type": "call_declined", "by": telegram_id, "session_id": session_id})
    return {"ok": True}


@router.post("/api/call/hangup")
async def hangup_call(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    from services.calls import CallService
    session_id = payload.get("session_id")
    result = await CallService().hangup(telegram_id, session_id)
    if not result.ok:
        _fail(result)
    await _publish(session_id, {"type": "call_ended", "by": telegram_id, "session_id": session_id})
    return {"ok": True}


@router.get("/api/call/state")
async def call_state(telegram_id: int = Depends(current_telegram_id)):
    """What the call room needs on load: who I am, who the polite peer is,
    and fresh TURN credentials."""
    from services.calls import CallService, ice_servers
    from services.chats import ChatService
    chat = await ChatService().active(telegram_id)
    if not chat:
        raise HTTPException(status_code=409, detail="NOT_CHATTING")
    state = await CallService().state(chat.session_id)
    if not state:
        raise HTTPException(status_code=409, detail="NO_CALL")
    return {
        "session_id": chat.session_id,
        "status": state["status"],
        "is_initiator": state["initiator"] == telegram_id,
        "telegram_id": telegram_id,
        "ice_servers": ice_servers(telegram_id),
        # Anonymity beats connectivity here: relay everything so neither side
        # ever sees the other's IP address.
        "force_relay": True,
    }


@router.get("/call/{token}")
async def enter_room(token: str):
    """The link handed to whoever accepted from Telegram. Burns the token,
    issues a normal session cookie and drops them straight into the room."""
    from services.calls import CallService
    data = await CallService().redeem_room_token(token)
    if not data:
        return RedirectResponse(url="/app.html", status_code=302)
    response = RedirectResponse(url="/call.html", status_code=302)
    response.set_cookie(
        SESSION_COOKIE, create_session_token(data["telegram_id"]),
        max_age=config.WEB_SESSION_MAX_AGE, httponly=True,
        secure=config.WEB_COOKIE_SECURE, samesite="lax")
    return response
