from html import escape
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
        InlineKeyboardButton(text='Понравилось', icon_custom_emoji_id="5350417283783084711", callback_data=f"v1:rate:{session_id}:1", style="success"),
        InlineKeyboardButton(text='Не понравилось', icon_custom_emoji_id="5348132683304156113", callback_data=f"v1:rate:{session_id}:-1", style="danger"),
    ]])


def ad_card_text(ad):
    lines = ["📢 <i>Реклама</i>", "", f"<b>{escape(ad['title'])}</b>"]
    if ad["subtitle"]:
        lines.append(escape(ad["subtitle"]))
    lines += ["", escape(ad["body"])]
    return "\n".join(lines)


def ad_keyboard(ad):
    # A plain url= button: Telegram gives the bot no event when it is tapped.
    # If the advertiser made the bot an admin, this is instead the campaign's
    # own tracked invite link, so a join (not a click) becomes measurable.
    url = ad.get("invite_link") or f"https://t.me/{ad['channel_username']}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перейти в канал", url=url, style="primary")],
        [InlineKeyboardButton(text="Пропустить", callback_data=f"v1:ad:{ad['impression_id']}:skip")],
    ])


@chat_router.callback_query(F.data.startswith("v1:ad:"))
async def cb_ad_skip(callback: CallbackQuery):
    await callback.answer()


@chat_router.callback_query(F.data.startswith("v1:contact:allow:"))
async def cb_allow_contact(callback: CallbackQuery):
    session_id = callback.data.removeprefix("v1:contact:allow:")
    chat = await ChatService().active(callback.from_user.id)
    if not chat or chat.session_id != session_id:
        await callback.answer("Диалог уже завершён.", show_alert=True)
        return
    from services.relay import RelayService
    await RelayService().allow_contacts(callback.from_user.id, session_id)
    await callback.answer("Разрешено для этого диалога")
    await callback.message.edit_text(
        "Предупреждение принято. Теперь отправь сообщение с контактом ещё раз.")


async def finish(message, user_id, reason="stop"):
    result = await ChatService().end_chat(user_id, reason=reason)
    if not result.ok:
        await message.answer("Ты сейчас не в чате.", reply_markup=main_menu_kb())
        return False
    chat = result.data["chat"]
    await message.answer("👋 Диалог завершён", reply_markup=main_menu_kb())
    # Every reason finish() is actually called with (stop/next/unreachable) is a
    # real "back at the menu" moment for the person looking at this message.
    from services.ads import AdService
    ad = await AdService().pick(user_id)
    if ad.ok:
        await message.answer(ad_card_text(ad.data), reply_markup=ad_keyboard(ad.data))
    if reason in {"stop", "next"}:
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
async def forward_message(message: Message, album=None):
    if message.text == "/delete":
        await delete_relayed_message(message)
        return
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
    content = message.text or message.caption
    if await service.contact_warning_required(message.from_user.id, result.data["session_id"], content):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Понимаю, отправить контакт",
                callback_data=f"v1:contact:allow:{result.data['session_id']}", style="danger")]])
        await message.answer(
            "⚠️ В сообщении найден контакт. После отправки собеседник сможет связаться с тобой вне бота. "
            "Если ты согласен, нажми кнопку и отправь сообщение ещё раз.", reply_markup=keyboard)
        return
    try:
        if album:
            if any(item.content_type not in service.SUPPORTED for item in album):
                await message.answer("Этот тип альбома не поддерживается.")
                return
            copied = await message.bot.copy_messages(chat_id=partner, from_chat_id=message.chat.id,
                message_ids=[item.message_id for item in album])
            if len(copied) == len(album):
                for source, target in zip(album, copied):
                    await service.remember(result.data["session_id"], message.from_user.id, source.message_id, partner, target.message_id)
        else:
            params = {}
            reply = getattr(message, "reply_to_message", None)
            if reply:
                target = await service.reply_target(result.data["session_id"], message.from_user.id, reply.message_id)
                if target:
                    from aiogram.types import ReplyParameters
                    params["reply_parameters"] = ReplyParameters(message_id=target, allow_sending_without_reply=True)
            copied = await message.bot.copy_message(chat_id=partner, from_chat_id=message.chat.id,
                message_id=message.message_id, **params)
            await service.remember(result.data["session_id"], message.from_user.id, message.message_id, partner, copied.message_id)
        await service.delivered(message.from_user.id,result.data["session_id"],message.chat.id,message.message_id)
        await service.remember_context(result.data["session_id"], message.from_user.id, content)
    except TelegramForbiddenError:
        await finish(message, message.from_user.id, "unreachable")
    except TelegramBadRequest:
        logger.warning("event=relay_failed chat_session_id=%s", result.data["session_id"])
        await message.answer("Не удалось передать сообщение. Попробуй другой формат.")


@chat_router.edited_message()
async def sync_edited_message(message: Message):
    if not (message.text or message.caption):
        return
    from services.relay import RelayService
    service = RelayService()
    chat = await ChatService().active(message.from_user.id)
    if not chat:
        return
    if await service.contact_warning_required(
            message.from_user.id, chat.session_id, message.text or message.caption):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Понимаю, разрешить контакты",
                callback_data=f"v1:contact:allow:{chat.session_id}", style="danger")]])
        await message.answer(
            "⚠️ Изменение содержит контакт и не передано собеседнику. Подтверди передачу, "
            "затем измени сообщение ещё раз.", reply_markup=keyboard)
        return
    target = await service.reply_target(chat.session_id, message.from_user.id, message.message_id)
    if not target:
        return
    partner = chat.user2_id if chat.user1_id == message.from_user.id else chat.user1_id
    try:
        if message.text is not None:
            await message.bot.edit_message_text(message.text, chat_id=partner,
                                                message_id=target, parse_mode=None)
        else:
            await message.bot.edit_message_caption(chat_id=partner, message_id=target,
                                                   caption=message.caption, parse_mode=None)
        await service.remember_context(chat.session_id, message.from_user.id,
                                       message.text or message.caption)
    except TelegramBadRequest:
        await message.answer("Не удалось синхронизировать изменение сообщения.")


@chat_router.message(Command("delete"))
async def delete_relayed_message(message: Message):
    reply = message.reply_to_message
    if not reply:
        await message.answer("Ответь командой /delete на сообщение, которое нужно удалить у собеседника.")
        return
    from services.relay import RelayService
    service = RelayService()
    chat = await ChatService().active(message.from_user.id)
    if not chat:
        await message.answer("Диалог уже завершён.")
        return
    target = await service.reply_target(chat.session_id, message.from_user.id, reply.message_id)
    if not target:
        await message.answer("Это сообщение уже нельзя удалить у собеседника.")
        return
    partner = chat.user2_id if chat.user1_id == message.from_user.id else chat.user1_id
    try:
        await message.bot.delete_message(partner, target)
        await message.answer("Сообщение удалено у собеседника.")
    except TelegramBadRequest:
        await message.answer("Telegram уже не позволяет удалить это сообщение.")
