"""
Re-engagement push notifications based on real events only.

Spec 12.4.1 — NO fake "someone is interested" messages.
Only real, verifiable data triggers notifications.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from utils.db import get_user_by_id, get_pending_reports, get_total_users
from services.chat_manager import get_online_count
from keyboards.inline import menu_kb

logger = logging.getLogger(__name__)

# Track pending reveal requests to notify
_pending_reveal_notifications: set[int] = set()


def queue_reveal_notification(user_id: int):
    """Queue a reveal notification for a user."""
    _pending_reveal_notifications.add(user_id)


async def send_post_chat_rating(bot: Bot, rated_user_id: int, is_positive: bool):
    """Notify user they were rated after a chat."""
    emoji = "👍" if is_positive else "👎"
    text = f"{emoji} Тебя оценили после последнего диалога!"
    try:
        await bot.send_message(
            rated_user_id,
            text,
            reply_markup=menu_kb(),
        )
    except Exception:
        pass


async def check_reveal_notifications(bot: Bot):
    """Send pending reveal notifications."""
    for user_id in list(_pending_reveal_notifications):
        _pending_reveal_notifications.discard(user_id)
        text = "❤️ Кто-то предложил раскрыться взаимно"
        try:
            await bot.send_message(user_id, text, reply_markup=menu_kb())
        except Exception:
            pass


async def check_inactive_users(bot: Bot, days: int = 3):
    """Notify users inactive for N+ days."""
    from datetime import date
    from sqlalchemy import select
    from models.base import async_session
    from models.user import User

    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        result = await session.execute(
            select(User).where(
                User.last_online < cutoff,
                User.is_banned == False,
            ).limit(50)
        )
        users = result.scalars().all()

    for user in users:
        online = await get_online_count()
        text = (
            f"👋 Скучаем! За это время в боте было много новых диалогов\n\n"
            f"🔥 Сейчас онлайн {online} человек — самое время для поиска"
        )
        try:
            await bot.send_message(
                user.telegram_id,
                text,
                reply_markup=menu_kb(),
            )
        except Exception:
            pass
        await asyncio.sleep(0.1)  # Rate limit


async def check_premium_expiry(bot: Bot):
    """Notify users whose Premium expires in 1 day."""
    tomorrow = datetime.utcnow() + timedelta(days=1)
    today = datetime.utcnow().date()
    tomorrow_date = tomorrow.date()

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
        text = "⏰ Твой Premium заканчивается завтра. Продлить?"
        try:
            from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Продлить", callback_data="premium")],
            ])
            await bot.send_message(user.telegram_id, text, reply_markup=kb)
        except Exception:
            pass


async def notification_loop(bot: Bot):
    """Background loop for periodic notifications."""
    while True:
        try:
            await check_reveal_notifications(bot)
            await check_premium_expiry(bot)
            # Check inactive users every 6 hours
            import time
            if int(time.time()) % 21600 < 300:
                await check_inactive_users(bot)
        except Exception as e:
            logger.error(f"Notification loop error: {e}")
        await asyncio.sleep(300)  # Check every 5 minutes
