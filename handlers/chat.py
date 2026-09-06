from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from handlers import chat_router
from keyboards import texts as T
from keyboards.reply import main_menu_kb
from services.chats import ChatService
from services.chat_manager import get_redis
from repositories.chats import ChatRepository
import config
import logging

logger = logging.getLogger(__name__)


def rating_keyboard(session_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Понравилось', callback_data=f"v1:rate:{session_id}:1", style="success"),
        InlineKeyboardButton(text='<tg-emoji emoji-id=\"5348132683304156113\">👎</tg-emoji> Не понравилось', callback_data=f"v1:rate:{session_id}:-1", style="danger"),
    ]])


async def finish(message, user_id, reason="stop"):
    result = await ChatService().end_chat(user_id, reason=reason)
    if not result.ok:
        await message.answer("Ты сейчас не в чате.", reply_markup=main_menu_kb())
        return False
    chat = result.data["chat"]
    for uid in (chat.user1_id, chat.user2_id):
        try:
            await message.bot.send_message(uid, "<tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji> Диалог завершён" if uid == user_id else "<tg-emoji emoji-id=\"5927208490870248283\">😢</tg-emoji> Собеседник покинул чат.", reply_markup=main_menu_kb())
            await message.bot.send_message(uid, "Оцени собеседника:", reply_markup=rating_keyboard(chat.session_id))
        except TelegramForbiddenError:
            pass
    if reason == "stop":
        from services.growth import GrowthService, track
        from handlers.premium import product_keyboard
        text=await GrowthService().upsell(user_id)
        if text:
            await message.answer(text,reply_markup=await product_keyboard(["premium_1m"]))
            await track(user_id,"premium_offer_shown",f"premium_offer:{chat.session_id}:{user_id}")
    return True


@chat_router.message(Command("stop"))
@chat_router.message(F.text == T.STOP)
async def cmd_stop(message: Message):
    await finish(message, message.from_user.id)


async def next_chat(message, user_id):
    from utils.db import is_premium
    from services.settings import bot_value
    from services.payments import consume_delay_skip
    r = await get_redis()
    delay = await bot_value("chat_delay_next", config.CHAT_DELAY_NEXT)
    if delay > 0 and not await is_premium(user_id) and not await r.set(f"cooldown:next:{user_id}", "1", ex=delay, nx=True) and not await consume_delay_skip(user_id):
        await message.answer(f"<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Подожди {max(1, await r.ttl(f'cooldown:next:{user_id}'))} сек.")
        from handlers.premium import product_keyboard
        await message.answer("Можно продолжить без задержки:", reply_markup=await product_keyboard(["skip_next_delay", "premium_1m"]))
        return
    if await finish(message, user_id, "next"):
        from handlers.search import begin_search
        await begin_search(message, user_id)


@chat_router.message(F.text == T.NEXT)
async def cmd_next(message: Message):
    await next_chat(message, message.from_user.id)


@chat_router.callback_query(F.data.in_({"chat:stop", "chat:next"}))
async def cb_chat_control(callback: CallbackQuery):
    await callback.answer()
    if callback.data == "chat:next":
        await next_chat(callback.message, callback.from_user.id)
    else:
        await finish(callback.message, callback.from_user.id)


@chat_router.message()
async def forward_message(message: Message):
    if message.text and (message.text.startswith("/") or message.text in T.SYSTEM_TEXTS):
        return
    from services.relay import RelayService
    service=RelayService()
    result=await service.prepare(message.from_user.id,message.content_type)
    if result.code=="NOT_CHATTING":
        return
    if not result.ok:
        await message.answer("Этот тип сообщения не поддерживается. Отправь текст, фото, голосовое или видео.")
        return
    partner = result.data["partner"]
    try:
        await message.bot.copy_message(chat_id=partner, from_chat_id=message.chat.id, message_id=message.message_id)
        await service.delivered(message.from_user.id,result.data["session_id"],message.chat.id,message.message_id)
    except TelegramForbiddenError:
        await finish(message, message.from_user.id, "unreachable")
    except TelegramBadRequest:
        logger.warning("event=relay_failed chat_session_id=%s", result.data["session_id"])
        await message.answer("Не удалось передать сообщение. Попробуй другой формат.")
