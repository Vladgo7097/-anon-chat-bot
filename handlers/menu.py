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
INVITE_CARD = {
    "ru": "👥 <b>Пригласи друга</b>\n\nПосле его первого диалога ты получишь <b>1 час Premium</b>, а друг — скидку 20% на первую покупку.\n\nТвоя ссылка:\n{link}\n\nПриглашено: {count} · Активировали: {activated}",
    "en": "👥 <b>Invite a friend</b>\n\nAfter their first chat you get <b>1 hour of Premium</b>, and they get 20% off their first purchase.\n\nYour link:\n{link}\n\nInvited: {count} · Activated: {activated}",
    "uk": "👥 <b>Запроси друга</b>\n\nПісля його першого діалогу ти отримаєш <b>1 годину Premium</b>, а друг — знижку 20% на першу покупку.\n\nТвоє посилання:\n{link}\n\nЗапрошено: {count} · Активували: {activated}",
}
SHARE_BUTTON = {"ru": "Поделиться ссылкой", "en": "Share the link", "uk": "Поділитись посиланням"}
REWARDS_BUTTON = {"ru": "Мои награды", "en": "My rewards", "uk": "Мої нагороди"}
NOT_ONBOARDED_YET = {"ru": "Сначала заверши регистрацию: /start",
                     "en": "Finish signup first: /start",
                     "uk": "Спочатку заверши реєстрацію: /start"}


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


@menu_router.message(F.text.in_(T.all_labels(T.INVITE)))
async def invite_friend(message: Message, state: FSMContext, lang: str = "ru"):
    # The referral program existed but lived only inside the rewards card --
    # nobody taps a menu item they cannot see, hence zero activations. This
    # puts the link, the counter and the actual reward one button away.
    from urllib.parse import quote
    from sqlalchemy import select
    from models.base import async_session
    from models.user import User
    await state.clear()
    async with async_session() as s:
        user = await s.scalar(select(User).where(User.telegram_id == message.from_user.id))
    if not user or not user.referral_code:
        await message.answer(T.tr(NOT_ONBOARDED_YET, lang))
        return
    bot = await message.bot.get_me()
    link = f"https://t.me/{bot.username}?start=ref_{user.referral_code}"
    await message.answer(
        T.tr(INVITE_CARD, lang).format(link=link, count=user.referrals_count,
                                       activated=user.referrals_activated),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=T.tr(SHARE_BUTTON, lang),
                                 url="https://t.me/share/url?url=" + quote(link, safe="")),
            InlineKeyboardButton(text=T.tr(REWARDS_BUTTON, lang),
                                 callback_data="profile:rewards", style="primary")]]))


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
