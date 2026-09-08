"""Calls from the Telegram side: /call to offer one, inline buttons to answer.

The conversation itself always happens in a browser -- Telegram has no way for
a bot to put two of its users into a voice call -- so accepting hands the
person a one-time link into the call room.
"""
import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from handlers import call_router
from services.calls import CallService, OFFER_TEXT

logger = logging.getLogger(__name__)


def offer_keyboard(session_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Принять звонок", callback_data=f"v1:call:yes:{session_id}"),
        InlineKeyboardButton(text="Отклонить", callback_data=f"v1:call:no:{session_id}"),
    ]])


def room_keyboard(token):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Войти в звонок",
                             url=f"https://{config.TURN_HOST}/call/{token}")]])


@call_router.message(Command("call"))
async def cmd_call(message: Message):
    result = await CallService().offer(message.from_user.id)
    if not result.ok:
        await message.answer({
            "PREMIUM_REQUIRED": "Звонки доступны с Premium. Оформить: /premium",
            "NOT_CHATTING": "Звонок можно предложить только собеседнику в активном диалоге.",
            "CALL_IN_PROGRESS": "Звонок уже идёт или предложен.",
        }.get(result.code, "Не удалось предложить звонок."))
        return
    session_id, partner = result.data["session_id"], result.data["partner"]
    from services.relay import RelayService
    await RelayService().publish_delivery(session_id, {
        "type": "call_offer", "from": message.from_user.id, "session_id": session_id})
    try:
        await message.bot.send_message(partner, OFFER_TEXT, reply_markup=offer_keyboard(session_id))
    except TelegramBadRequest:
        logger.warning("event=call_offer_undelivered chat_session_id=%s", session_id)
    await message.answer("🎙 Предложение отправлено. Ждём ответа собеседника.")


@call_router.callback_query(F.data.startswith("v1:call:yes:"))
async def cb_call_accept(callback: CallbackQuery):
    session_id = callback.data.removeprefix("v1:call:yes:")
    service = CallService()
    result = await service.accept(callback.from_user.id, session_id)
    if not result.ok:
        await callback.answer("Звонок уже недоступен", show_alert=True)
        return
    await callback.answer()
    from services.relay import RelayService
    await RelayService().publish_delivery(session_id, {
        "type": "call_accepted", "by": callback.from_user.id, "session_id": session_id})
    token = await service.mint_room_token(callback.from_user.id, session_id)
    try:
        await callback.message.edit_text(
            "✅ Звонок принят. Открой комнату — ссылка одноразовая и живёт 5 минут.",
            reply_markup=room_keyboard(token))
    except TelegramBadRequest:
        await callback.message.answer(
            "✅ Звонок принят. Открой комнату:", reply_markup=room_keyboard(token))
    # The initiator may also be sitting in Telegram, not on the site: give them
    # their own way in, otherwise only one side ever reaches the room.
    state = result.data["state"]
    initiator_token = await service.mint_room_token(state["initiator"], session_id)
    try:
        await callback.bot.send_message(
            state["initiator"], "✅ Собеседник принял звонок. Открой комнату:",
            reply_markup=room_keyboard(initiator_token))
    except TelegramBadRequest:
        logger.warning("event=call_link_undelivered chat_session_id=%s", session_id)


@call_router.callback_query(F.data.startswith("v1:call:no:"))
async def cb_call_decline(callback: CallbackQuery):
    session_id = callback.data.removeprefix("v1:call:no:")
    result = await CallService().decline(callback.from_user.id, session_id)
    await callback.answer("Отклонено" if result.ok else "Звонок уже недоступен")
    if not result.ok:
        return
    from services.relay import RelayService
    await RelayService().publish_delivery(session_id, {
        "type": "call_declined", "by": callback.from_user.id, "session_id": session_id})
    try:
        await callback.message.edit_text("Звонок отклонён.")
    except TelegramBadRequest:
        pass
