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

NOT_IN_CHAT = {"ru": "Ты сейчас не в чате.", "en": "You're not in a chat right now.", "uk": "Ти зараз не в чаті."}
CHAT_ENDED = {"ru": "👋 Диалог завершён", "en": "👋 Chat ended", "uk": "👋 Діалог завершено"}
NOT_YOUR_CHAT = {"ru": "Диалог уже завершён.", "en": "This chat has already ended.", "uk": "Діалог вже завершено."}
CONTACT_ALLOWED_TOAST = {"ru": "Разрешено для этого диалога", "en": "Allowed for this chat", "uk": "Дозволено для цього діалогу"}
CONTACT_ALLOWED_EDIT = {"ru": "Предупреждение принято. Теперь отправь сообщение с контактом ещё раз.",
                         "en": "Warning accepted. Now send the message with the contact again.",
                         "uk": "Попередження прийнято. Тепер надішли повідомлення з контактом ще раз."}
WAIT_COOLDOWN = {"ru": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Подожди {sec} сек.",
                  "en": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Wait {sec} sec.",
                  "uk": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Зачекай {sec} сек."}
CONTINUE_NO_DELAY = {"ru": "Можно продолжить без задержки:", "en": "You can continue without the delay:",
                      "uk": "Можна продовжити без затримки:"}
UNSUPPORTED_TYPE = {"ru": "Этот тип сообщения не поддерживается. Отправь текст, фото, голосовое или видео.",
                     "en": "This message type isn't supported. Send text, a photo, a voice message or a video.",
                     "uk": "Цей тип повідомлення не підтримується. Надішли текст, фото, голосове або відео."}
CONTACT_WARNING = {"ru": "⚠️ В сообщении найден контакт. После отправки собеседник сможет связаться с тобой вне бота. "
                          "Если ты согласен, нажми кнопку и отправь сообщение ещё раз.",
                    "en": "⚠️ A contact was found in the message. Once sent, your match will be able to reach you outside the bot. "
                          "If you agree, tap the button and send the message again.",
                    "uk": "⚠️ У повідомленні знайдено контакт. Після відправки співрозмовник зможе зв'язатися з тобою поза ботом. "
                          "Якщо ти згоден, натисни кнопку і надішли повідомлення ще раз."}
UNSUPPORTED_ALBUM = {"ru": "Этот тип альбома не поддерживается.", "en": "This album type isn't supported.",
                      "uk": "Цей тип альбому не підтримується."}
RELAY_FAILED = {"ru": "Не удалось передать сообщение. Попробуй другой формат.",
                 "en": "Couldn't deliver the message. Try a different format.",
                 "uk": "Не вдалося передати повідомлення. Спробуй інший формат."}
EDIT_CONTACT_WARNING = {"ru": "⚠️ Изменение содержит контакт и не передано собеседнику. Подтверди передачу, "
                                "затем измени сообщение ещё раз.",
                         "en": "⚠️ The edit contains a contact and was not delivered. Confirm delivery, "
                               "then edit the message again.",
                         "uk": "⚠️ Зміна містить контакт і не передана співрозмовнику. Підтверди передачу, "
                               "потім зміни повідомлення ще раз."}
CONTACT_ALLOWED_EDIT_TOAST = {"ru": "Понимаю, разрешить контакты", "en": "I understand, allow contacts", "uk": "Розумію, дозволити контакти"}
SYNC_FAILED = {"ru": "Не удалось синхронизировать изменение сообщения.",
               "en": "Couldn't sync the message edit.", "uk": "Не вдалося синхронізувати зміну повідомлення."}
DELETE_USAGE = {"ru": "Ответь командой /delete на сообщение, которое нужно удалить у собеседника.",
                 "en": "Reply with /delete to the message you want to delete for your match.",
                 "uk": "Відповідай командою /delete на повідомлення, яке потрібно видалити у співрозмовника."}
ALREADY_ENDED = {"ru": "Диалог уже завершён.", "en": "This chat has already ended.", "uk": "Діалог вже завершено."}
CANNOT_DELETE = {"ru": "Это сообщение уже нельзя удалить у собеседника.",
                  "en": "This message can no longer be deleted for your match.",
                  "uk": "Це повідомлення вже не можна видалити у співрозмовника."}
DELETED_OK = {"ru": "Сообщение удалено у собеседника.", "en": "Message deleted for your match.", "uk": "Повідомлення видалено у співрозмовника."}
CANNOT_DELETE_NOW = {"ru": "Telegram уже не позволяет удалить это сообщение.",
                      "en": "Telegram no longer allows deleting this message.",
                      "uk": "Telegram вже не дозволяє видалити це повідомлення."}
UNDERSTAND_SEND_CONTACT = {"ru": "Понимаю, отправить контакт", "en": "I understand, send the contact", "uk": "Розумію, надіслати контакт"}


def rating_keyboard(session_id, lang="ru"):
    buttons = T.tr(T.RATING_BUTTONS, lang)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=buttons["yes"], icon_custom_emoji_id="5350417283783084711", callback_data=f"v1:rate:{session_id}:1", style="success"),
        InlineKeyboardButton(text=buttons["no"], icon_custom_emoji_id="5348132683304156113", callback_data=f"v1:rate:{session_id}:-1", style="danger"),
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
async def cb_allow_contact(callback: CallbackQuery, lang: str = "ru"):
    session_id = callback.data.removeprefix("v1:contact:allow:")
    chat = await ChatService().active(callback.from_user.id)
    if not chat or chat.session_id != session_id:
        await callback.answer(T.tr(NOT_YOUR_CHAT, lang), show_alert=True)
        return
    from services.relay import RelayService
    await RelayService().allow_contacts(callback.from_user.id, session_id)
    await callback.answer(T.tr(CONTACT_ALLOWED_TOAST, lang))
    await callback.message.edit_text(T.tr(CONTACT_ALLOWED_EDIT, lang))


async def finish(message, user_id, reason="stop", lang="ru"):
    result = await ChatService().end_chat(user_id, reason=reason)
    if not result.ok:
        await message.answer(T.tr(NOT_IN_CHAT, lang), reply_markup=main_menu_kb(lang))
        return False
    chat = result.data["chat"]
    await message.answer(T.tr(CHAT_ENDED, lang), reply_markup=main_menu_kb(lang))
    # Every reason finish() is actually called with (stop/next/unreachable) is a
    # real "back at the menu" moment for the person looking at this message.
    from services.ads import AdService
    ad = await AdService().pick(user_id)
    if ad.ok:
        await message.answer(ad_card_text(ad.data), reply_markup=ad_keyboard(ad.data))
    if reason in {"stop", "next"}:
        from services.growth import GrowthService, track
        from handlers.premium import product_keyboard
        text=await GrowthService().upsell(user_id, lang)
        if text:
            await message.answer(text,reply_markup=await product_keyboard(["premium_1m"]))
            await track(user_id,"premium_offer_shown",f"premium_offer:{chat.session_id}:{user_id}")
    return True


@chat_router.message(Command("stop"))
@chat_router.message(F.text.in_(T.all_labels(T.STOP)))
async def cmd_stop(message: Message, lang: str = "ru"):
    await finish(message, message.from_user.id, lang=lang)


async def next_chat(message, user_id, lang="ru"):
    from utils.db import is_premium
    from services.settings import bot_value
    from services.payments import consume_delay_skip
    r = await get_redis()
    delay = await bot_value("chat_delay_next", config.CHAT_DELAY_NEXT)
    if delay > 0 and not await is_premium(user_id) and not await r.set(f"cooldown:next:{user_id}", "1", ex=delay, nx=True) and not await consume_delay_skip(user_id):
        await message.answer(T.tr(WAIT_COOLDOWN, lang).format(sec=max(1, await r.ttl(f'cooldown:next:{user_id}'))))
        from handlers.premium import product_keyboard
        await message.answer(T.tr(CONTINUE_NO_DELAY, lang), reply_markup=await product_keyboard(["skip_next_delay", "premium_1m"]))
        return
    if await finish(message, user_id, "next", lang=lang):
        from handlers.search import begin_search
        await begin_search(message, user_id, lang)


@chat_router.message(F.text.in_(T.all_labels(T.NEXT)))
async def cmd_next(message: Message, lang: str = "ru"):
    await next_chat(message, message.from_user.id, lang)


@chat_router.callback_query(F.data.in_({"chat:stop", "chat:next"}))
async def cb_chat_control(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    if callback.data == "chat:next":
        await next_chat(callback.message, callback.from_user.id, lang)
    else:
        await finish(callback.message, callback.from_user.id, lang=lang)


@chat_router.message()
async def forward_message(message: Message, album=None, lang: str = "ru"):
    if message.text == "/delete":
        await delete_relayed_message(message, lang=lang)
        return
    if message.text and (message.text.startswith("/") or message.text in T.SYSTEM_TEXTS):
        return
    from services.relay import RelayService
    service=RelayService()
    result=await service.prepare(message.from_user.id,message.content_type)
    if result.code=="NOT_CHATTING":
        return
    if not result.ok:
        await message.answer(T.tr(UNSUPPORTED_TYPE, lang))
        return
    partner = result.data["partner"]
    content = message.text or message.caption
    if await service.contact_warning_required(message.from_user.id, result.data["session_id"], content):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=T.tr(UNDERSTAND_SEND_CONTACT, lang),
                callback_data=f"v1:contact:allow:{result.data['session_id']}", style="danger")]])
        await message.answer(T.tr(CONTACT_WARNING, lang), reply_markup=keyboard)
        return
    try:
        if album:
            if any(item.content_type not in service.SUPPORTED for item in album):
                await message.answer(T.tr(UNSUPPORTED_ALBUM, lang))
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
        await finish(message, message.from_user.id, "unreachable", lang=lang)
    except TelegramBadRequest:
        logger.warning("event=relay_failed chat_session_id=%s", result.data["session_id"])
        await message.answer(T.tr(RELAY_FAILED, lang))


@chat_router.edited_message()
async def sync_edited_message(message: Message, lang: str = "ru"):
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
            InlineKeyboardButton(text=T.tr(CONTACT_ALLOWED_EDIT_TOAST, lang),
                callback_data=f"v1:contact:allow:{chat.session_id}", style="danger")]])
        await message.answer(T.tr(EDIT_CONTACT_WARNING, lang), reply_markup=keyboard)
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
        await message.answer(T.tr(SYNC_FAILED, lang))


@chat_router.message(Command("delete"))
async def delete_relayed_message(message: Message, lang: str = "ru"):
    reply = message.reply_to_message
    if not reply:
        await message.answer(T.tr(DELETE_USAGE, lang))
        return
    from services.relay import RelayService
    service = RelayService()
    chat = await ChatService().active(message.from_user.id)
    if not chat:
        await message.answer(T.tr(ALREADY_ENDED, lang))
        return
    target = await service.reply_target(chat.session_id, message.from_user.id, reply.message_id)
    if not target:
        await message.answer(T.tr(CANNOT_DELETE, lang))
        return
    partner = chat.user2_id if chat.user1_id == message.from_user.id else chat.user1_id
    try:
        await message.bot.delete_message(partner, target)
        await message.answer(T.tr(DELETED_OK, lang))
    except TelegramBadRequest:
        await message.answer(T.tr(CANNOT_DELETE_NOW, lang))
