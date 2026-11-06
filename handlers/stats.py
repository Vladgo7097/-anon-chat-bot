from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import stats_router
from keyboards import texts as T
from keyboards.inline import stats_kb
from utils.db import get_user_stats
from utils.rich_messages import card_blocks, details_block, send_rich

NOT_FOUND = {
    "ru": "Профиль не найден",
    "en": "Profile not found",
    "uk": "Профіль не знайдено",
}
LEVELS = {
    "novice": {"ru": "Новичок", "en": "Newcomer", "uk": "Новачок"},
    "beginner": {"ru": "Начинающий", "en": "Beginner", "uk": "Початківець"},
    "regular": {
        "ru": '<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Регулярный',
        "en": '<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Regular',
        "uk": '<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Регулярний',
    },
    "active": {
        "ru": '<tg-emoji emoji-id="5303040337059543238">🔥</tg-emoji> Активный собеседник',
        "en": '<tg-emoji emoji-id="5303040337059543238">🔥</tg-emoji> Active chatter',
        "uk": '<tg-emoji emoji-id="5303040337059543238">🔥</tg-emoji> Активний співрозмовник',
    },
    "legend": {
        "ru": '<tg-emoji emoji-id="5283130719806175947">🏆</tg-emoji> Легенда',
        "en": '<tg-emoji emoji-id="5283130719806175947">🏆</tg-emoji> Legend',
        "uk": '<tg-emoji emoji-id="5283130719806175947">🏆</tg-emoji> Легенда',
    },
}
STATS_TEXT = {
    "ru": '<tg-emoji emoji-id="5884336303415236350">📊</tg-emoji> Твоя статистика\n\n'
    '<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Всего диалогов: {chats}\n'
    '<tg-emoji emoji-id="5258258882022612173">⏱</tg-emoji> Общее время: {time}\n'
    '<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> Рейтинг: {rating}% положительных\n'
    '<tg-emoji emoji-id="5303040337059543238">🔥</tg-emoji> Дней подряд: {streak}\n'
    '<tg-emoji emoji-id="5283130719806175947">🏆</tg-emoji> Уровень: {level}\n',
    "en": '<tg-emoji emoji-id="5884336303415236350">📊</tg-emoji> Your stats\n\n'
    '<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Total chats: {chats}\n'
    '<tg-emoji emoji-id="5258258882022612173">⏱</tg-emoji> Total time: {time}\n'
    '<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> Rating: {rating}% positive\n'
    '<tg-emoji emoji-id="5303040337059543238">🔥</tg-emoji> Day streak: {streak}\n'
    '<tg-emoji emoji-id="5283130719806175947">🏆</tg-emoji> Level: {level}\n',
    "uk": '<tg-emoji emoji-id="5884336303415236350">📊</tg-emoji> Твоя статистика\n\n'
    '<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Усього діалогів: {chats}\n'
    '<tg-emoji emoji-id="5258258882022612173">⏱</tg-emoji> Загальний час: {time}\n'
    '<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> Рейтинг: {rating}% позитивних\n'
    '<tg-emoji emoji-id="5303040337059543238">🔥</tg-emoji> Днів поспіль: {streak}\n'
    '<tg-emoji emoji-id="5283130719806175947">🏆</tg-emoji> Рівень: {level}\n',
}


STATS_SUMMARY = {
    "ru": "📖 Показать подробно",
    "en": "📖 Show details",
    "uk": "📖 Показати подробиці",
}


def rich_stats_blocks(lang, text):
    """Bot API 10.1+: статистика как rich message; перечень цифр внутри
    сворачиваемого RichBlockDetails, заголовок остаётся видимым."""
    lines = [l for l in text.split("\n") if l.strip()]
    blocks = card_blocks(lines[0]) if lines else []
    if len(lines) > 1:
        blocks.append(details_block(T.tr(STATS_SUMMARY, lang), "\n".join(lines[1:])))
    return blocks


def format_stats(stats: dict, lang: str = "ru") -> str:
    hours = stats.get("total_chat_seconds", 0) // 3600
    minutes = (stats.get("total_chat_seconds", 0) % 3600) // 60
    time_str = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"

    chats_count = stats.get("chats_count", 0)
    if chats_count >= 100:
        level_key = "legend"
    elif chats_count >= 50:
        level_key = "active"
    elif chats_count >= 20:
        level_key = "regular"
    elif chats_count >= 5:
        level_key = "beginner"
    else:
        level_key = "novice"

    return T.tr(STATS_TEXT, lang).format(
        chats=chats_count,
        time=time_str,
        rating=stats.get("rating_pct", 0),
        streak=stats.get("streak", 0),
        level=T.tr(LEVELS[level_key], lang),
    )


@stats_router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery, lang: str = "ru"):
    stats = await get_user_stats(callback.from_user.id)
    if not stats:
        await callback.answer(T.tr(NOT_FOUND, lang), show_alert=True)
        return
    await send_rich(
        callback.bot,
        callback.message.chat.id,
        rich_stats_blocks(lang, format_stats(stats, lang)),
        stats_kb(lang),
    )
    await callback.answer()


@stats_router.message(F.text.in_(T.all_labels(T.STATS)))
async def cmd_stats(message: Message, lang: str = "ru"):
    stats = await get_user_stats(message.from_user.id)
    if not stats:
        await message.answer(T.tr(NOT_FOUND, lang))
        return
    await send_rich(
        message.bot,
        message.chat.id,
        rich_stats_blocks(lang, format_stats(stats, lang)),
        stats_kb(lang),
    )
