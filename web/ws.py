"""Single per-user WebSocket carrying both search status and chat delivery.

Reuses the exact same building blocks handlers/search.py and handlers/chat.py
use for the bot -- MatchingService.enqueue/find/cancel, ChatService.active/
end_chat -- so a match or a chat is identical in the database whether either
side is on Telegram, on the website, or one of each. This module only adds a
second way to reach those functions and a way to push their results to a
browser instead of a Telegram chat.
"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

import config
from services.chat_manager import get_online_count, get_redis
from services.chats import ChatService
from services.matching import MatchingService
from services.onboarding import step_for
from web import auth

logger = logging.getLogger(__name__)
router = APIRouter()

SEARCH_POLL_SECONDS = 2
CHAT_POLL_TIMEOUT = 1.0


async def _run_search(websocket, telegram_id, state, stop):
    result = await MatchingService().enqueue(telegram_id)
    if result.code not in ("SEARCH_STARTED", "ALREADY_SEARCHING"):
        await websocket.send_json({"type": "search_error", "code": result.code})
        state["mode"] = "idle"
        return
    from services.settings import bot_value
    started = time.monotonic()
    while not stop.is_set() and state["mode"] == "searching":
        chat = await MatchingService().find(telegram_id)
        if chat:
            state["mode"], state["session_id"] = "chatting", chat.session_id
            await websocket.send_json({"type": "matched", "session_id": chat.session_id})
            return
        elapsed = int(time.monotonic() - started)
        timeout = await bot_value("search_timeout", config.SEARCH_TIMEOUT)
        if elapsed >= timeout:
            await MatchingService().cancel(telegram_id)
            state["mode"] = "idle"
            await websocket.send_json({"type": "search_timeout"})
            return
        await websocket.send_json({
            "type": "search_status", "elapsed": elapsed, "online": await get_online_count(),
        })
        await asyncio.sleep(SEARCH_POLL_SECONDS)


async def _run_chat(websocket, telegram_id, state, stop):
    session_id = state["session_id"]
    channel = f"chat:{session_id}"
    try:
        pubsub = (await get_redis()).pubsub()
        await pubsub.subscribe(channel)
    except RedisError:
        logger.warning("event=web_chat_pubsub_unavailable chat_session_id=%s", session_id)
        await asyncio.sleep(1)
        return
    try:
        while not stop.is_set() and state["mode"] == "chatting" and state["session_id"] == session_id:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=CHAT_POLL_TIMEOUT)
            except RedisError:
                await asyncio.sleep(1)
                continue
            if not message or message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except (ValueError, TypeError):
                continue
            if payload.get("type") == "chat_ended":
                state["mode"], state["session_id"] = "idle", None
            await websocket.send_json(payload)
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except RedisError:
            pass


async def _receiver(websocket, telegram_id, state, stop):
    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                continue
            action = msg.get("action") if isinstance(msg, dict) else None
            if action == "search" and state["mode"] == "idle":
                state["mode"] = "searching"
            elif action == "cancel" and state["mode"] == "searching":
                await MatchingService().cancel(telegram_id)
                state["mode"] = "idle"
                await websocket.send_json({"type": "search_cancelled"})
            elif action == "end" and state["mode"] == "chatting":
                await ChatService().end_chat(telegram_id, reason="stop")
                # The chat_ended pub/sub event (published by end_chat itself)
                # flips state["mode"] back to idle from inside _run_chat.
    finally:
        stop.set()


@router.websocket("/ws/session")
async def session_ws(websocket: WebSocket):
    await websocket.accept()
    token = websocket.cookies.get(auth.SESSION_COOKIE)
    telegram_id = auth.read_session_token(token) if token else None
    if telegram_id is None:
        await websocket.send_json({"type": "error", "code": "NOT_AUTHENTICATED"})
        await websocket.close(code=4401)
        return

    from repositories.users import UserRepository
    user = await UserRepository().get(telegram_id)
    if user is None or user.deleted_at is not None:
        await websocket.send_json({"type": "error", "code": "NOT_AUTHENTICATED"})
        await websocket.close(code=4401)
        return
    if step_for(user) != "MENU":
        await websocket.send_json({"type": "onboarding_required", "bot_username": config.BOT_USERNAME})
        await websocket.close(code=4403)
        return

    state = {"mode": "idle", "session_id": None}
    stop = asyncio.Event()
    receiver_task = asyncio.create_task(_receiver(websocket, telegram_id, state, stop))

    try:
        chat = await ChatService().active(telegram_id)
        if chat:
            state["mode"], state["session_id"] = "chatting", chat.session_id
            await websocket.send_json({"type": "matched", "session_id": chat.session_id})

        while not stop.is_set():
            if state["mode"] == "idle":
                await asyncio.sleep(0.2)
            elif state["mode"] == "searching":
                await _run_search(websocket, telegram_id, state, stop)
            elif state["mode"] == "chatting":
                await _run_chat(websocket, telegram_id, state, stop)
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        receiver_task.cancel()
        if state["mode"] == "searching":
            await MatchingService().cancel(telegram_id)
