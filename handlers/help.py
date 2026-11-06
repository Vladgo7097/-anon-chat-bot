from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from handlers import help_router
from keyboards import texts as T
from keyboards.inline import help_kb
from utils.rich_messages import card_blocks, details_block, send_rich
import config

HELP_TEXT = {
    "ru": '<tg-emoji emoji-id="5312464908351201524">❓</tg-emoji> Помощь\n\n/start — открыть бота\n/search — найти собеседника\n/stop — завершить диалог\n/profile — мой профиль\n/report — пожаловаться\n/rules — правила',
    "en": '<tg-emoji emoji-id="5312464908351201524">❓</tg-emoji> Help\n\n/start — open the bot\n/search — find a match\n/stop — end the chat\n/profile — my profile\n/report — report\n/rules — rules',
    "uk": '<tg-emoji emoji-id="5312464908351201524">❓</tg-emoji> Допомога\n\n/start — відкрити бота\n/search — знайти співрозмовника\n/stop — завершити діалог\n/profile — мій профіль\n/report — поскаржитись\n/rules — правила',
}
SUPPORT_SUFFIX = {
    "ru": "\n\nПоддержка: @{u}",
    "en": "\n\nSupport: @{u}",
    "uk": "\n\nПідтримка: @{u}",
}
EDIT_NOTE = {
    "ru": "\n\nРедактирование текста и подписи синхронизируется с собеседником. "
    "Чтобы удалить сообщение у собеседника, ответь на него командой /delete. "
    "Telegram не сообщает боту об обычном удалении сообщения пользователем.",
    "en": "\n\nEditing text or a caption is synced with your match. "
    "To delete a message for your match, reply to it with /delete. "
    "Telegram does not tell the bot when a user deletes a message normally.",
    "uk": "\n\nРедагування тексту й підпису синхронізується зі співрозмовником. "
    "Щоб видалити повідомлення у співрозмовника, відповідай на нього командою /delete. "
    "Telegram не повідомляє боту про звичайне видалення повідомлення користувачем.",
}


DETAILS_SUMMARY = {
    "ru": "📖 Показать подробности",
    "en": "📖 Show details",
    "uk": "📖 Показати подробиці",
}


def rich_help_blocks(lang, text):
    """Bot API 10.1+: помощь как rich message, длинная часть — внутри
    сворачиваемого RichBlockDetails (клик по «Показать подробности»)."""
    blocks, rest = [], text.split("\n")
    # Заголовок (первая строка с эмодзи) остаётся видимым.
    blocks += card_blocks(rest[0]) if rest else []
    body = "\n".join(rest[1:]).strip()
    if body:
        blocks.append(details_block(T.tr(DETAILS_SUMMARY, lang), body))
    return blocks


def help_text(lang):
    text = T.tr(HELP_TEXT, lang)
    if config.SUPPORT_USERNAME:
        text += T.tr(SUPPORT_SUFFIX, lang).format(u=config.SUPPORT_USERNAME.lstrip("@"))
    return text + T.tr(EDIT_NOTE, lang)


@help_router.message(Command("help"))
@help_router.message(F.text.in_(T.all_labels(T.HELP)))
async def cmd_help(message: Message, lang: str = "ru"):
    await send_rich(
        message.bot,
        message.chat.id,
        rich_help_blocks(lang, help_text(lang)),
        help_kb(lang),
    )


@help_router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await send_rich(
        callback.bot,
        callback.message.chat.id,
        rich_help_blocks(lang, help_text(lang)),
        help_kb(lang),
    )
