"""
Re-engagement push notifications based on real events only.

Spec 12.4.1 — NO fake "someone is interested" messages.
Only real, verifiable data triggers notifications.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from utils.db import get_user_by_id
from services.chat_manager import get_online_count
from keyboards.inline import menu_kb

logger = logging.getLogger(__name__)

_pending_reveal_notifications: set[int] = set()

# Cooldown tracking — prevent duplicate notifications
_notified_inactive: dict[int, datetime] = {}  # user_id -> last notification time
_notified_premium_expiry: dict[int, datetime] = {}


def queue_reveal_notification(user_id: int):
    _pending_reveal_notifications.add(user_id)


async def send_post_chat_rating(bot: Bot, rated_user_id: int, is_positive: bool):
    emoji = "👍" if is_positive else "👎"
    text = f"{emoji} Тебя оценили после последнего диалога!"
    try:
        await bot.send_message(rated_user_id, text, reply_markup=menu_kb())
    except Exception:
        pass


async def check_reveal_notifications(bot: Bot):
    for user_id in list(_pending_reveal_notifications):
        _pending_reveal_notifications.discard(user_id)
        text = "❤️ Кто-то предложил раскрыться взаимно"
        try:
            await bot.send_message(user_id, text, reply_markup=menu_kb())
        except Exception:
            pass


async def check_inactive_users(bot: Bot, days: int = 3):
    """Notify users inactive for N+ days. Cooldown: 24 hours."""
    from sqlalchemy import select
    from models.base import async_session
    from models.user import User

    cutoff = datetime.utcnow() - timedelta(days=days)
    now = datetime.utcnow()
    cooldown = timedelta(hours=24)

    async with async_session() as session:
        result = await session.execute(
            select(User).where(
                User.last_online < cutoff,
                User.is_banned == False,
            ).limit(50)
        )
        users = result.scalars().all()

    for user in users:
        # BUG FIX: check cooldown to avoid spamming
        last_notified = _notified_inactive.get(user.telegram_id)
        if last_notified and (now - last_notified) < cooldown:
            continue

        online = await get_online_count()
        text = (
            f"👋 Скучаем! За это время в боте было много новых диалогов\n\n"
            f"🔥 Сейчас онлайн {online} человек — самое время для поиска"
        )
        try:
            await bot.send_message(user.telegram_id, text, reply_markup=menu_kb())
            _notified_inactive[user.telegram_id] = now
        except Exception:
            pass
        await asyncio.sleep(0.1)


async def check_premium_expiry(bot: Bot):
    """Notify users whose Premium expires in 1 day. Cooldown: 12 hours."""
    now = datetime.utcnow()
    tomorrow = now + timedelta(days=1)
    today = now.date()
    tomorrow_date = tomorrow.date()
    cooldown = timedelta(hours=12)

    from sqlalchemy import select
    from models.base import async_session
    from models.user import User

    async with async_session() as session:
        result = await session.execute(
            select(User).where(
                User.is_premium == True,
                User.premium_expires >= datetime.combine(today, datetime.min.time()),
                User.premium_expires <= datetime.combine(tomorrow_date, datetime.max.time()),
            )
        )
        users = result.scalars().all()

    for user in users:
        # BUG FIX: check cooldown to avoid duplicates
        last_notified = _notified_premium_expiry.get(user.telegram_id)
        if last_notified and (now - last_notified) < cooldown:
            continue

        text = "⏰ Твой Premium заканчивается завтра. Продлить?"
        try:
            from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Продлить", callback_data="premium")],
            ])
            await bot.send_message(user.telegram_id, text, reply_markup=kb)
            _notified_premium_expiry[user.telegram_id] = now
        except Exception:
            pass


async def notification_loop(bot: Bot):
    """Background loop for periodic notifications."""
    last_inactive_check = 0
    while True:
        try:
            await check_reveal_notifications(bot)
            await check_premium_expiry(bot)
            # BUG FIX: use proper timer instead of modulo
            import time
            now = time.time()
            if now - last_inactive_check >= 21600:  # Every 6 hours
                await check_inactive_users(bot)
                last_inactive_check = now
        except Exception as e:
            logger.error(f"Notification loop error: {e}")
        await asyncio.sleep(300)
