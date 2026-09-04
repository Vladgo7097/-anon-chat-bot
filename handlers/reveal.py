from aiogram import F
from aiogram.types import CallbackQuery

from handlers import reveal_router
from services.chat_manager import get_partner, request_reveal, check_reveal
from utils.db import get_or_create_user, is_premium, add_payment
import config


@reveal_router.callback_query(F.data == "chat:reveal")
async def cb_reveal(callback: CallbackQuery):
    partner_id = await get_partner(callback.from_user.id)
    if not partner_id:
        await callback.answer("Ты сейчас не в чате.", show_alert=True)
        return

    from keyboards.inline import reveal_offer_kb
    await callback.message.edit_text(
        "❤️ Предложить раскрыться взаимно?\n\n"
        "Если оба нажмут — покажем @username друг друга.",
        reply_markup=reveal_offer_kb(partner_id),
    )
    await callback.answer()


@reveal_router.callback_query(F.data == "reveal:mutual")
async def cb_reveal_mutual(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await callback.answer("Собеседник не найден.", show_alert=True)
        return

    is_mutual = await request_reveal(user_id, partner_id)

    if is_mutual:
        partner_db = await get_or_create_user(partner_id)
        username = partner_db.username if partner_db and partner_db.username else "скрыт"

        await callback.message.edit_text(
            f"🎉 Вы оба хотите общаться открыто!\n\n"
            f"👤 Собеседник: @{username}\n\n"
            f"Приятного общения! 😊"
        )

        my_db = await get_or_create_user(user_id)
        my_username = my_db.username if my_db and my_db.username else "скрыт"
        try:
            await callback.bot.send_message(
                partner_id,
                f"🎉 Вы оба хотите общаться открыто!\n\n"
                f"👤 Собеседник: @{my_username}\n\n"
                f"Приятного общения! 😊"
            )
        except Exception:
            pass
    else:
        # Queue notification for partner
        from services.notifications import queue_reveal_notification
        queue_reveal_notification(partner_id)

        await callback.message.edit_text(
            "❤️ Запрос отправлен\n"
            "Ждём, согласится ли собеседник...\n\n"
            "💡 Хочешь узнать реакцию собеседника сразу?",
            reply_markup=callback.message.reply_markup,
        )

    await callback.answer()


@reveal_router.callback_query(F.data == "reveal:pay")
async def cb_reveal_pay(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await callback.answer("Собеседник не найден.", show_alert=True)
        return

    premium = await is_premium(user_id)
    if premium:
        partner_db = await get_or_create_user(partner_id)
        username = partner_db.username if partner_db and partner_db.username else "скрыт"
        await callback.message.edit_text(
            f"👤 Собеседник: @{username}\n\n(Бесплатно с Premium)"
        )
        await callback.answer()
        return

    from aiogram.types import LabeledPrice
    await callback.bot.send_invoice(
        chat_id=user_id,
        title="Узнать собеседника",
        description="Раскрыть username собеседника без взаимности",
        payload=f"reveal:{user_id}:{partner_id}",
        currency="XTR",
        prices=[LabeledPrice(label="Раскрытие собеседника", amount=config.STARS_REVEAL_ONE)],
    )
    await callback.answer()


@reveal_router.callback_query(F.data == "reveal:check")
async def cb_reveal_check(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await callback.answer("Собеседник не найден.", show_alert=True)
        return

    has_revealed = await check_reveal(user_id, partner_id)
    if has_revealed:
        await callback.answer("❤️ Собеседник тоже хочет раскрыться!", show_alert=True)
    else:
        await callback.answer("⏳ Собеседник пока не ответил.", show_alert=True)
