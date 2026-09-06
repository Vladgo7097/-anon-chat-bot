from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import stats_router
from keyboards.inline import stats_kb
from utils.db import get_user_stats


def format_stats(stats: dict) -> str:
    hours = stats.get("total_chat_seconds", 0) // 3600
    minutes = (stats.get("total_chat_seconds", 0) % 3600) // 60
    time_str = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"

    streak = stats.get("streak", 0)
    level = "Новичок"
    if stats.get("chats_count", 0) >= 100:
        level = "<tg-emoji emoji-id=\"5283130719806175947\">🏆</tg-emoji> Легенда"
    elif stats.get("chats_count", 0) >= 50:
        level = "<tg-emoji emoji-id=\"5303040337059543238\">🔥</tg-emoji> Активный собеседник"
    elif stats.get("chats_count", 0) >= 20:
        level = "<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Регулярный"
    elif stats.get("chats_count", 0) >= 5:
        level = "Начинающий"

    return (
        f"<tg-emoji emoji-id=\"5884336303415236350\">📊</tg-emoji> Твоя статистика\n\n"
        f"<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Всего диалогов: {stats.get('chats_count', 0)}\n"
        f"<tg-emoji emoji-id=\"5258258882022612173\">⏱</tg-emoji> Общее время: {time_str}\n"
        f"<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Рейтинг: {stats.get('rating_pct', 0)}% положительных\n"
        f"<tg-emoji emoji-id=\"5303040337059543238\">🔥</tg-emoji> Дней подряд: {streak}\n"
        f"<tg-emoji emoji-id=\"5283130719806175947\">🏆</tg-emoji> Уровень: {level}\n"
    )


@stats_router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery):
    stats = await get_user_stats(callback.from_user.id)
    if not stats:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    await callback.message.edit_text(format_stats(stats), reply_markup=stats_kb())
    await callback.answer()


@stats_router.message(F.text == "Статистика")
async def cmd_stats(message: Message):
    stats = await get_user_stats(message.from_user.id)
    if not stats:
        await message.answer("Профиль не найден.")
        return
    await message.answer(format_stats(stats), reply_markup=stats_kb())
