from aiogram import F
from aiogram.types import CallbackQuery

from handlers import rating_router
from utils.db import add_rating
from keyboards.inline import menu_kb


_last_partner: dict[int, dict] = {}


def store_last_partner(user_id: int, partner_id: int):
    _last_partner[user_id] = {"partner_id": partner_id}


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

        from services.notifications import send_post_chat_rating
        await send_post_chat_rating(callback.bot, partner_id, is_positive)

        from handlers.referral import activate_referral
        await activate_referral(user_id)
        await activate_referral(partner_id)

    text = "👍 Спасибо за оценку!" if is_positive else "👎 Принято. Постараемся лучше!"
    await callback.message.edit_text(text, reply_markup=menu_kb())
    await callback.answer()
