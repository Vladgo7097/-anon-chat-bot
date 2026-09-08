"""Account area for the website: profile, search settings, Premium, stats.

Every mutation goes through the same service the bot uses (ProfileService,
SettingsService, PaymentService), so the rules -- Premium gating, "can't
change settings mid-chat", photo limits, price snapshots -- hold identically
on both surfaces. The web layer only translates HTTP to those calls.
"""
import json

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import BufferedInputFile, LabeledPrice
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import config
from web.auth import current_telegram_id
from web.routes_chat import outbound_bot, MAX_PHOTO_BYTES, SUPPORTED_IMAGE_TYPES

router = APIRouter(prefix="/api")

ACHIEVEMENT_TITLES = {
    "first_chat": "Первый диалог",
    "reveal_1": "Первое взаимное раскрытие",
    "reveal_5": "5 взаимных раскрытий",
    "chats_10": "10 диалогов",
    "chats_100": "100 диалогов",
    "streak_7": "7 дней подряд",
}


async def _load_user(telegram_id):
    from repositories.users import UserRepository
    user = await UserRepository().get(telegram_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    return user


def _service_error(result):
    codes = {
        "ACTIVE_CHAT": (409, "ACTIVE_CHAT"),
        "PREMIUM_REQUIRED": (402, "PREMIUM_REQUIRED"),
        "PHOTO_LIMIT": (402, "PHOTO_LIMIT"),
        "INVALID": (400, "INVALID"),
        "NOT_FOUND": (404, "NOT_FOUND"),
        "NOT_AVAILABLE": (409, "NOT_AVAILABLE"),
    }
    status, detail = codes.get(result.code, (400, result.code))
    raise HTTPException(status_code=status, detail=detail)


@router.get("/profile")
async def get_profile(telegram_id: int = Depends(current_telegram_id)):
    from repositories.chats import premium_active
    user = await _load_user(telegram_id)
    photos = json.loads(user.extra_photos or "[]")
    return {
        "anon_id": user.anon_id,
        "gender": user.gender,
        "age": user.age,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "photo_count": (1 if user.profile_photo else 0) + len(photos),
        "photo_limit": config.MAX_PROFILE_PHOTOS,
        "free_photos": config.FREE_PROFILE_PHOTOS,
        "is_premium": premium_active(user),
        "premium_expires": user.premium_expires.isoformat() if user.premium_expires else None,
    }


@router.post("/profile/field")
async def set_profile_field(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    from services.profile import ProfileService
    field, value = payload.get("field"), payload.get("value")
    if field == "age":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="INVALID")
    result = await ProfileService().edit(telegram_id, field, value)
    if not result.ok:
        _service_error(result)
    return {"ok": True}


@router.post("/profile/photo")
async def upload_profile_photo(file: UploadFile = File(...), telegram_id: int = Depends(current_telegram_id)):
    """Profile photos live as Telegram file_ids (that is how the bot stores and
    shows them), so a web upload is pushed through the bot to the person's own
    chat to obtain one. They get the copy as their own confirmation."""
    if file.content_type not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="UNSUPPORTED_TYPE")
    data = await file.read()
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="INVALID_SIZE")
    try:
        sent = await outbound_bot().send_photo(
            telegram_id, BufferedInputFile(data, filename=file.filename or "photo.jpg"),
            caption="Фото из профиля на сайте.")
    except TelegramForbiddenError:
        raise HTTPException(status_code=410, detail="BOT_BLOCKED")
    except TelegramBadRequest:
        raise HTTPException(status_code=422, detail="UPLOAD_FAILED")
    from services.profile import ProfileService
    result = await ProfileService().add_photo(telegram_id, sent.photo[-1].file_id)
    if not result.ok:
        _service_error(result)
    return {"ok": True}


@router.post("/profile/delete")
async def delete_profile(telegram_id: int = Depends(current_telegram_id)):
    from services.profile import ProfileService
    result = await ProfileService().delete(telegram_id)
    if not result.ok:
        _service_error(result)
    return {"ok": True}


@router.get("/settings")
async def get_settings(telegram_id: int = Depends(current_telegram_id)):
    from repositories.chats import premium_active
    user = await _load_user(telegram_id)
    return {
        "gender": user.settings_gender_filter,
        "age_min": user.settings_age_min,
        "age_max": user.settings_age_max,
        "is_premium": premium_active(user),
        "min_age": config.MIN_AGE,
        "max_age": config.MAX_AGE,
    }


@router.post("/settings/search")
async def save_settings(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    from services.settings import SettingsService
    gender = payload.get("gender")
    age_min, age_max = payload.get("age_min"), payload.get("age_max")
    age_range = None
    if age_min is not None and age_max is not None:
        try:
            age_range = (int(age_min), int(age_max))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="INVALID")
    result = await SettingsService().save_search(telegram_id, gender=gender, age_range=age_range)
    if not result.ok:
        _service_error(result)
    return {"ok": True}


@router.get("/premium")
async def get_premium(telegram_id: int = Depends(current_telegram_id)):
    from repositories.chats import premium_active
    from services.growth import first_purchase_discount
    from services.payments import PaymentService
    user = await _load_user(telegram_id)
    active = premium_active(user)
    # Same grant the bot's premium screen makes, so the discount does not
    # depend on which surface the person happened to open the tariffs from.
    discount = None if active else await first_purchase_discount(telegram_id)
    products = await PaymentService().products()
    return {
        "is_premium": active,
        "premium_expires": user.premium_expires.isoformat() if user.premium_expires else None,
        "discount_percent": discount,
        "products": [
            {"code": p.code, "title": p.title, "price": p.price_stars, "type": p.product_type,
             "duration_seconds": p.duration_seconds,
             "price_final": max(1, p.price_stars*(100-discount)//100)
                            if discount and p.code.startswith("premium_") else p.price_stars}
            for p in products
        ],
    }


@router.post("/premium/buy")
async def buy_premium(payload: dict, telegram_id: int = Depends(current_telegram_id)):
    """Telegram Stars can only be charged inside Telegram, so the site cannot
    take the payment itself: it asks the bot to send the invoice to the
    person's chat, and they pay it there. Same PaymentService flow as the
    bot's own buy button, including the invoice claim that stops duplicates."""
    from services.payments import PaymentService
    code = payload.get("code")
    if not code or code.startswith("reveal_"):
        raise HTTPException(status_code=400, detail="INVALID")
    service = PaymentService()
    result = await service.create(telegram_id, code)
    if not result.ok:
        _service_error(result)
    payment = result.data["payment"]
    if not await service.claim_invoice(telegram_id, payment.id):
        return {"ok": True, "code": "INVOICE_ALREADY_SENT"}
    try:
        invoice = await outbound_bot().send_invoice(
            chat_id=telegram_id, title=result.data["title"],
            description="Улучшение анонимного чата. Приоритет не гарантирует наличие подходящего "
                        "собеседника; контакты требуют взаимного согласия.",
            payload=payment.payload, currency="XTR",
            prices=[LabeledPrice(label=result.data["title"], amount=payment.stars_amount)])
    except TelegramForbiddenError:
        raise HTTPException(status_code=410, detail="BOT_BLOCKED")
    except TelegramBadRequest:
        raise HTTPException(status_code=422, detail="INVOICE_FAILED")
    await service.invoice_sent(payment.id, invoice.message_id)
    return {"ok": True, "code": "INVOICE_SENT", "stars": payment.stars_amount}


@router.get("/stats")
async def get_stats(telegram_id: int = Depends(current_telegram_id)):
    from sqlalchemy import select
    from models.base import async_session
    from models.operations import Achievement
    from utils.db import get_user_stats
    stats = await get_user_stats(telegram_id)
    if not stats:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    user = await _load_user(telegram_id)
    async with async_session() as s:
        unlocked = list((await s.scalars(
            select(Achievement).where(Achievement.user_id == telegram_id))).all())
    return {
        "chats_count": stats["chats_count"],
        "total_chat_seconds": stats["total_chat_seconds"],
        "likes": stats["likes"],
        "dislikes": stats["dislikes"],
        "streak": stats["streak"],
        "created_at": stats["created_at"].isoformat() if stats["created_at"] else None,
        "referral_link": f"https://t.me/{config.BOT_USERNAME}?start=ref_{user.referral_code}",
        "referrals_count": user.referrals_count,
        "referrals_activated": user.referrals_activated,
        "achievements": [
            {"code": a.achievement_code,
             "title": ACHIEVEMENT_TITLES.get(a.achievement_code, a.achievement_code),
             "unlocked_at": a.unlocked_at.isoformat() if a.unlocked_at else None}
            for a in unlocked
        ],
    }
