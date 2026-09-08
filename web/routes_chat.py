"""Sending a message from the website.

Mirrors handlers/chat.py::forward_message step for step (same permission
check, same flood limit, same contact-text gate) so a message sent from the
site is held to exactly the rules a message sent through Telegram is -- it
just also reaches the Telegram partner through a plain Bot instance instead
of an aiogram Message object, and fans out to any connected website peer
through RelayService.publish_delivery.
"""
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import config
from web.auth import current_telegram_id

router = APIRouter(prefix="/api/chat")

MAX_PHOTO_BYTES = 10 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_outbound_bot: Bot | None = None


def outbound_bot() -> Bot:
    """A Bot instance used only to push outgoing messages. No Dispatcher, no
    polling: it never touches incoming updates, so it cannot race the bot
    process (workers/maintenance.py, handlers/*) over who handles what."""
    global _outbound_bot
    if _outbound_bot is None:
        _outbound_bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _outbound_bot


async def _prepare_and_gate(telegram_id, content_type, text_for_contact_check):
    from services.relay import RelayService
    from services.chat_manager import check_flood
    service = RelayService()
    result = await service.prepare(telegram_id, content_type)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.code)
    if await check_flood(telegram_id):
        raise HTTPException(status_code=429, detail="RATE_LIMITED")
    if await service.contact_warning_required(telegram_id, result.data["session_id"], text_for_contact_check):
        raise HTTPException(status_code=422, detail="CONTACT_WARNING")
    return service, result.data["partner"], result.data["session_id"]


async def _unreachable(telegram_id):
    from services.chats import ChatService
    await ChatService().end_chat(telegram_id, reason="unreachable")
    raise HTTPException(status_code=410, detail="PARTNER_UNREACHABLE")


@router.post("/send")
async def send_text(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="EMPTY")
    if len(text) > 4096:
        raise HTTPException(status_code=400, detail="TOO_LONG")
    service, partner, session_id = await _prepare_and_gate(telegram_id, "text", text)
    try:
        await outbound_bot().send_message(partner, text)
    except TelegramForbiddenError:
        await _unreachable(telegram_id)
    except TelegramBadRequest:
        raise HTTPException(status_code=422, detail="DELIVERY_FAILED")
    await service.delivered(telegram_id, session_id, telegram_id, 0)
    await service.remember_context(session_id, telegram_id, text)
    await service.publish_delivery(session_id, {
        "type": "message", "from": telegram_id, "content_type": "text", "text": text,
    })
    return {"ok": True}


@router.post("/send-photo")
async def send_photo(file: UploadFile = File(...), telegram_id: int = Depends(current_telegram_id)):
    if file.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_TYPE")
    data = await file.read()
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="INVALID_SIZE")
    service, partner, session_id = await _prepare_and_gate(telegram_id, "photo", None)
    photo = BufferedInputFile(data, filename=file.filename or "photo.jpg")
    try:
        await outbound_bot().send_photo(partner, photo)
    except TelegramForbiddenError:
        await _unreachable(telegram_id)
    except TelegramBadRequest:
        raise HTTPException(status_code=422, detail="DELIVERY_FAILED")
    await service.delivered(telegram_id, session_id, telegram_id, 0)
    await service.publish_delivery(session_id, {
        "type": "message", "from": telegram_id, "content_type": "photo",
    })
    return {"ok": True}
