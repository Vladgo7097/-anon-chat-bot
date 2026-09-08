from html import escape
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError
from handlers import reveal_router
from services.chats import ChatService
from services.reveal import RevealService
from keyboards import texts as T


def reveal_keyboard(sid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Предложить раскрыться", icon_custom_emoji_id="5348232622898167572", callback_data=f"v1:reveal:{sid}", style="primary")]])


def reveal_request_keyboard(sid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Согласиться", callback_data=f"v1:reveal:{sid}", style="success"),
        InlineKeyboardButton(text="Отказать", callback_data=f"v1:reveal:no:{sid}", style="danger"),
    ]])


async def show_reveal(message, user_id):
    chat = await ChatService().active(user_id)
    if not chat:
        await message.answer("Ты сейчас не в чате.")
        return
    await message.answer("<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Хочешь продолжить общение открыто?\n\nКонтакт станет виден только после согласия обоих собеседников.", reply_markup=reveal_keyboard(chat.session_id))
    from handlers.premium import product_keyboard
    from utils.db import is_premium
    if not await is_premium(user_id):
        await message.answer("Дополнительные возможности запроса:", reply_markup=await product_keyboard(["reveal_priority_request", "reveal_status"], chat.session_id))
    else:
        await message.answer("Включено в Premium:",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Приоритетный запрос", icon_custom_emoji_id="5350618807943576963",callback_data=f"v1:rp:{chat.session_id}"),
            InlineKeyboardButton(text="Статус", icon_custom_emoji_id="5350427505805238170",callback_data=f"v1:rs:{chat.session_id}")]]))


@reveal_router.callback_query(F.data.startswith("v1:rp:"))
@reveal_router.callback_query(F.data.startswith("v1:rs:"))
async def cb_reveal_upgrade(callback: CallbackQuery):
    parts=callback.data.split(":")
    await callback.answer()
    await paid_reveal(callback.message,callback.from_user.id,parts[-1],"reveal_priority_request" if parts[1]=="rp" else "reveal_status")


@reveal_router.message(F.text == T.REVEAL)
async def cmd_reveal(message: Message):
    await show_reveal(message, message.from_user.id)


@reveal_router.callback_query(F.data.in_({"chat:reveal", "reveal:pay", "reveal:mutual", "reveal:check"}))
async def cb_old_reveal(callback: CallbackQuery):
    await callback.answer()
    await show_reveal(callback.message, callback.from_user.id)


@reveal_router.callback_query(F.data.startswith("v1:reveal:"))
async def cb_consent(callback: CallbackQuery):
    if callback.data.startswith("v1:reveal:no:"):
        result = await RevealService().decline(
            callback.from_user.id, callback.data.removeprefix("v1:reveal:no:"))
        await callback.answer("Отказ отправлен" if result.ok else "Запрос уже недоступен")
        if result.ok:
            await callback.message.edit_text(
                "Ты отказался раскрывать контакт. Диалог можно продолжить анонимно.")
        return
    sid = callback.data.split(":")[-1]
    result = await RevealService().consent(callback.from_user.id, sid)
    await callback.answer()
    if not result.ok:
        await callback.message.answer("Запрос уже отправлен или диалог завершён.")
        return
    if result.code == "MUTUAL":
        profiles = result.data["profiles"]
        for uid in profiles:
            other = next((value for key, value in profiles.items() if key != uid), {})
            username = other.get("username")
            text = f"@{escape(username)}" if username else "контакт в Telegram не настроен"
            caption = (f"<tg-emoji emoji-id=\"5994502837327892086\">🎉</tg-emoji> "
                       f"Вы оба хотите общаться открыто!\n\n"
                       f"<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Собеседник: {text}")
            try:
                if other.get("photo"):
                    await callback.bot.send_photo(uid, other["photo"], caption=caption)
                else:
                    await callback.bot.send_message(uid, caption)
            except TelegramForbiddenError:
                pass
    else:
        await callback.message.answer("<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Запрос отправлен. Ждём согласия собеседника.")


async def paid_reveal(message, user_id, session_id, kind):
    # Consent is still required. Payment only upgrades the request/status view.
    from services.reveal import upgrade_request
    result = await upgrade_request(user_id, session_id, kind)
    if not result.ok:
        await message.answer("Нужен Premium или отдельная покупка для этой функции." if result.code == "NOT_PAID"
                              else "Диалог уже завершён; контакт не раскрывается.")
        return
    if kind == "reveal_status":
        await message.answer({"MUTUAL": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Согласие взаимно.", "PENDING": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Ожидается ответ.", "DECLINED": "Собеседник отказался раскрывать контакт.", "NOT_REQUESTED": "Запрос ещё не отправлен."}.get(result.code, "Запрос завершён."))
    else:
        await message.answer("<tg-emoji emoji-id=\"5350618807943576963\">⚡</tg-emoji> Приоритетный запрос доступен. Подтверди своё согласие:", reply_markup=reveal_keyboard(session_id))
