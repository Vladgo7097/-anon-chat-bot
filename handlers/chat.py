import time
from aiogram import F, Bot
from aiogram.types import Message, CallbackQuery

from handlers import chat_router
from keyboards.inline import chat_kb, report_reasons_kb, stop_kb, reveal_offer_kb, menu_kb, search_kb
from keyboards.reply import chat_kb_reply, search_kb_reply, main_menu_kb
from services.chat_manager import get_partner, end_chat, refresh_chat_ttl
from utils.db import get_or_create_user, check_ban, is_premium
import config

# Track when user last pressed "Next" for cooldown
_last_next: dict[int, float] = {}

CONTENT_MAP = {
    "text": lambda m: m.text,
    "photo": lambda m: m.photo[-1].file_id if m.photo else None,
    "voice": lambda m: m.voice.file_id if m.voice else None,
    "sticker": lambda m: m.sticker.file_id if m.sticker else None,
    "video": lambda m: m.video.file_id if m.video else None,
    "video_note": lambda m: m.video_note.file_id if m.video_note else None,
    "animation": lambda m: m.animation.file_id if m.animation else None,
}


@chat_router.message(F.text == "🛑 Стоп")
async def cmd_stop(message: Message):
    user_id = message.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await message.answer("Ты сейчас не в чате.", reply_markup=main_menu_kb())
        return

    await end_chat(user_id)

    # Store partner for rating
    from handlers.rating import store_last_partner
    store_last_partner(user_id, partner_id)

    await message.answer(
        "👋 Диалог завершён\n\nОцени собеседника:",
        reply_markup=stop_kb(),
    )

    try:
        await message.bot.send_message(
            partner_id,
            "😢 Собеседник покинул чат. Ищем нового?",
            reply_markup=stop_kb(),
        )
    except Exception:
        pass


@chat_router.callback_query(F.data == "chat:stop")
async def cb_stop(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await callback.answer("Ты сейчас не в чате.", show_alert=True)
        return

    await end_chat(user_id)

    from handlers.rating import store_last_partner
    store_last_partner(user_id, partner_id)

    await callback.message.edit_text(
        "👋 Диалог завершён\n\nОцени собеседника:",
        reply_markup=stop_kb(),
    )

    try:
        await callback.bot.send_message(
            partner_id,
            "😢 Собеседник покинул чат. Ищем нового?",
            reply_markup=stop_kb(),
        )
    except Exception:
        pass
    await callback.answer()


@chat_router.callback_query(F.data == "chat:next")
async def cb_next(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await callback.answer("Ты сейчас не в чате.", show_alert=True)
        return

    # Cooldown check for free users
    premium = await is_premium(user_id)
    if not premium:
        now = time.time()
        last = _last_next.get(user_id, 0)
        remaining = config.CHAT_DELAY_NEXT - (now - last)
        if remaining > 0:
            await callback.answer(f"⏳ Подожди {int(remaining)} сек.", show_alert=True)
            return
        _last_next[user_id] = now

    await end_chat(user_id)
    await callback.answer("Ищем нового собеседника...")

    from handlers.search import cb_search_start
    await cb_search_start(callback)


@chat_router.callback_query(F.data == "chat:continue")
async def cb_continue(callback: CallbackQuery):
    await callback.answer("Продолжай общаться!")


# ── Message forwarding ───────────────────────────────────────────

@chat_router.message(F.text == "⏭ Следующий")
async def cmd_next(message: Message):
    user_id = message.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        await message.answer("Ты сейчас не в чате.", reply_markup=main_menu_kb())
        return

    # Cooldown check for free users
    premium = await is_premium(user_id)
    if not premium:
        now = time.time()
        last = _last_next.get(user_id, 0)
        remaining = config.CHAT_DELAY_NEXT - (now - last)
        if remaining > 0:
            await message.answer(f"⏳ Подожди {int(remaining)} сек. перед поиском нового собеседника.")
            return
        _last_next[user_id] = now

    await end_chat(user_id)
    from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
    await message.answer(
        "Нажми для нового поиска:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Найти собеседника", callback_data="search:start")],
        ]),
    )


@chat_router.message(F.text == "🚩 Пожаловаться")
async def cmd_report(message: Message):
    partner_id = await get_partner(message.from_user.id)
    if not partner_id:
        await message.answer("Ты сейчас не в чате.")
        return
    await message.answer("🚩 На что жалуетесь?", reply_markup=report_reasons_kb())


@chat_router.message(F.text == "❤️ Раскрыть себя")
async def cmd_reveal(message: Message):
    partner_id = await get_partner(message.from_user.id)
    if not partner_id:
        await message.answer("Ты сейчас не в чате.")
        return
    await message.answer(
        "❤️ Предложить раскрыться взаимно?",
        reply_markup=reveal_offer_kb(partner_id),
    )


# ── Generic message forwarding ──────────────────────────────────

IGNORE_MESSAGES = {
    "⏭ Следующий", "🛑 Стоп", "🚩 Пожаловаться", "❤️ Раскрыть себя",
    "🔍 Найти собеседника", "👤 Мой профиль", "⚙️ Настройки поиска",
    "⭐ Premium", "📊 Статистика", "❓ Помощь", "🏠 Меню",
}


@chat_router.message(F.content_type.in_({
    "text", "photo", "voice", "sticker", "video", "video_note", "animation",
}))
async def forward_message(message: Message):
    user_id = message.from_user.id
    partner_id = await get_partner(user_id)
    if not partner_id:
        return

    if message.text and (message.text.startswith("/") or message.text in IGNORE_MESSAGES):
        return

    await refresh_chat_ttl(user_id)

    copy_msg = None
    if message.photo:
        copy_msg = message
    elif message.voice:
        copy_msg = message
    elif message.sticker:
        copy_msg = message
    elif message.video:
        copy_msg = message
    elif message.video_note:
        copy_msg = message
    elif message.animation:
        copy_msg = message

    try:
        if copy_msg:
            await message.bot.copy_message(
                chat_id=partner_id,
                from_chat_id=user_id,
                message_id=message.message_id,
            )
        elif message.text:
            await message.bot.send_message(partner_id, message.text)
    except Exception:
        pass
