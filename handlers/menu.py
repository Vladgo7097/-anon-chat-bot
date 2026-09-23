from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers import menu_router
from keyboards import texts as T
from keyboards.reply import main_menu_kb
from services.chat_manager import set_online
import config

MENU_STATUS = {
    "ru": "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Главное меню\n\nСтатус: <tg-emoji emoji-id=\"5215522595922779944\">🟢</tg-emoji> Онлайн",
    "en": "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Main menu\n\nStatus: <tg-emoji emoji-id=\"5215522595922779944\">🟢</tg-emoji> Online",
    "uk": "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Головне меню\n\nСтатус: <tg-emoji emoji-id=\"5215522595922779944\">🟢</tg-emoji> Онлайн",
}
NO_INPUT = {"ru": "Нет незавершённого ввода.", "en": "There's nothing to cancel.", "uk": "Немає незавершеного вводу."}
CANCELLED = {"ru": "Ввод отменён.", "en": "Input cancelled.", "uk": "Ввід скасовано."}
CHAT_PROMPT = {
    "ru": "💬 Наш чат — большой чат для общения, заходи пообщаться.\n\nЖми «Открыть чат» 👇",
    "en": "💬 Our chat — a big hangout chat, drop in for a talk.\n\nTap “Open the chat” 👇",
    "uk": "💬 Наш чат — великий чат для спілкування, заходь поговорити.\n\nТисни «Відкрити чат» 👇",
}
CHAT_OPEN = {"ru": "Открыть чат", "en": "Open the chat", "uk": "Відкрити чат"}


@menu_router.message(F.text.in_(T.all_labels(T.CHAT)))
async def open_community_chat(message: Message, state: FSMContext, lang: str = "ru"):
    # Reply keyboards cannot carry a url (Bot API KeyboardButton has no such
    # field), so the tap arrives as plain text and we answer with a real
    # inline link button -- the only kind Telegram opens on tap.
    await state.clear()
    await message.answer(
        T.tr(CHAT_PROMPT, lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=T.tr(CHAT_OPEN, lang),
                                 url=config.COMMUNITY_CHAT_URL, style="primary")]]))


@menu_router.message(F.text.in_(T.all_labels(T.MENU)))
async def cmd_menu(message: Message, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await set_online(message.from_user.id)
    await message.answer(T.tr(MENU_STATUS, lang), reply_markup=main_menu_kb(lang))


@menu_router.message(Command("cancel"))
async def cancel_input(message: Message, state: FSMContext, lang: str = "ru"):
    if await state.get_state() is None:
        await message.answer(T.tr(NO_INPUT, lang))
        return
    await state.clear()
    await message.answer(T.tr(CANCELLED, lang))
