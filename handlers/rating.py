from aiogram import F
from aiogram.types import CallbackQuery

from handlers import rating_router
from utils.db import add_rating, save_chat, get_user_by_id
from keyboards.inline import menu_kb
from services.chat_manager import get_partner


_last_partner: dict[int, dict] = {}


def store_last_partner(user_id: int, partner_id: int, chat_id: int = 0):
    _last_partner[user_id] = {"partner_id": partner_id, "chat_id": chat_id}


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
        await add_rating(partner_id, is_positive)
        await save_chat(user_id, partner_id)

        # BUG FIX: notify the rated user
        from services.notifications import send_post_chat_rating
        await send_post_chat_rating(callback.bot, partner_id, is_positive)

    text = "👍 Спасибо за оценку!" if is_positive else "👎 Принято. Постараемся лучше!"
    await callback.message.edit_text(text, reply_markup=menu_kb())
    await callback.answer()
