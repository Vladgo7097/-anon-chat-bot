from aiogram import F
from aiogram.types import CallbackQuery

from handlers import report_router
from services.chat_manager import get_partner, end_chat
from utils.db import save_report, get_user_by_id
from keyboards.inline import search_kb, menu_kb
from keyboards.reply import main_menu_kb

REASON_MAP = {
    "report:18plus": "Контент 18+",
    "report:insult": "Оскорбления",
    "report:spam": "Спам/реклама",
    "report:minor": "Несовершеннолетний",
    "report:other": "Другое",
}


@report_router.callback_query(F.data.startswith("report:"))
async def cb_report(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await get_partner(user_id)

    if not partner_id:
        await callback.answer("Ты сейчас не в чате.", show_alert=True)
        return

    reason = REASON_MAP.get(callback.data, "Другое")
    await save_report(user_id, partner_id, reason)

    # End chat and notify
    await end_chat(user_id)

    await callback.message.edit_text(
        "✅ Жалоба отправлена модераторам.\nСобеседник заблокирован для тебя.",
    )
    await callback.message.answer(
        "Ищем нового собеседника...",
        reply_markup=search_kb,
    )

    # Notify partner
    try:
        await callback.bot.send_message(
            partner_id,
            "😢 Собеседник покинул чат. Ищем нового?",
        )
    except Exception:
        pass

    await callback.answer()
