import json
from datetime import date, datetime
from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import menu_router
from utils.db import get_or_create_user, update_user
import config


async def check_and_update_streak(user_id: int) -> dict:
    """Check and update daily streak. Returns streak info."""
    user = await get_or_create_user(user_id)
    today = date.today()
    result = {"is_new_day": False, "streak": 0, "reward": None}

    if user.last_active_date == today:
        return result  # Already checked in today

    yesterday = date.fromordinal(today.toordinal() - 1)
    if user.last_active_date == yesterday:
        user.streak_days += 1
    elif user.last_active_date != today:
        user.streak_days = 1  # Reset streak

    user.last_active_date = today
    await update_user(user_id, streak_days=user.streak_days, last_active_date=user.last_active_date)

    result["is_new_day"] = True
    result["streak"] = user.streak_days

    # Check rewards
    rewards_claimed = json.loads(user.streak_rewards_claimed or "[]")
    if user.streak_days >= 30 and "30d" not in rewards_claimed:
        result["reward"] = {"type": "30d", "discount": config.STRIK_DISCOUNT_30D, "badge": "🏆"}
        rewards_claimed.append("30d")
    elif user.streak_days >= 7 and "7d" not in rewards_claimed:
        result["reward"] = {"type": "7d", "discount": config.STRIK_DISCOUNT_7D, "badge": "🔥"}
        rewards_claimed.append("7d")
    elif user.streak_days >= 3 and "3d" not in rewards_claimed:
        result["reward"] = {"type": "3d", "discount": config.STRIK_DISCOUNT_3D, "badge": "⭐"}
        rewards_claimed.append("3d")

    if result["reward"]:
        await update_user(user_id, streak_rewards_claimed=json.dumps(rewards_claimed))

    return result


STREAK_TEXT_TEMPLATE = """🔥 Стрик: {streak} дней подряд!

День 3: скидка 10% на разовую покупку
День 7: скидка 20% на Premium + бейдж 🔥
День 30: скидка 30% на Premium (12 мес) + бейдж 🏆

Заходи каждый день, чтобы не потерять стрик 🔥"""


async def show_streak_notification(bot, user_id: int):
    """Send streak notification if there's a reward."""
    info = await check_and_update_streak(user_id)
    if info["reward"]:
        r = info["reward"]
        text = (
            f"🎉 Стрик {info['streak']} дней!\n\n"
            f"Ты получил скидку {r['discount']}% {r['badge']}\n"
            f"Скидка применится автоматически к следующей покупке."
        )
        try:
            await bot.send_message(user_id, text)
        except Exception:
            pass
