from aiogram import F
from aiogram.types import CallbackQuery

from handlers import rating_router
from utils.db import add_rating, save_chat, get_user_by_id, get_or_create_user
from keyboards.inline import menu_kb


_last_partner: dict[int, dict] = {}


def store_last_partner(user_id: int, partner_id: int, chat_started: float = 0):
    _last_partner[user_id] = {"partner_id": partner_id, "chat_started": chat_started}


def pop_last_partner(user_id: int) -> dict | None:
    return _last_partner.pop(user_id, None)


@rating_router.callback_query(F.data.startswith("rate:"))
async def cb_rate(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    is_positive = data == "rate:plus"

    info = pop_last_partner(user_id)
    if info:
        partner_id = info["partner_id"]
        chat_started = info.get("chat_started", 0)
        duration = int(__import__("time").time() - chat_started) if chat_started else 0

        await add_rating(partner_id, is_positive)
        await save_chat(user_id, partner_id, duration)

        # BUG FIX: update total_chat_seconds for both users
        from utils.db import update_user
        await update_user(user_id, total_chat_seconds=duration)
        await update_user(partner_id, total_chat_seconds=duration)

        from services.notifications import send_post_chat_rating
        await send_post_chat_rating(callback.bot, partner_id, is_positive)

        from handlers.referral import activate_referral
        await activate_referral(user_id)
        await activate_referral(partner_id)

    text = "👍 Спасибо за оценку!" if is_positive else "👎 Принято. Постараемся лучше!"
    await callback.message.edit_text(text, reply_markup=menu_kb())
    await callback.answer()
