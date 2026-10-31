"""Preview-карточки: фото+bio до открытия чата, обе стороны подтверждают.

Коллбэки pv:{yes|no}:{offer_id}. Согласие одного молчит до второго;
взаимное «да» открывает чат той же материей match() и гасит поисковые
циклы обеих сторон. Карточка собирается воркером outbox В МОМЕНТ
отправки: био и фото могли измениться между созданием оффера и
доставкой.
"""

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from handlers import preview_router
from keyboards import texts as T
from models.base import async_session
from models.preview import PreviewOffer
from models.user import User
from utils.db import user_lang


def preview_request_keyboard(offer_id, lang):
    labels = T.tr(T.PREVIEW_TEXTS, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels["yes"],
                    callback_data=f"pv:yes:{offer_id}",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text=labels["no"],
                    callback_data=f"pv:no:{offer_id}",
                    style="danger",
                ),
            ]
        ]
    )


def preview_caption(partner, lang):
    labels = T.tr(T.PREVIEW_TEXTS, lang)
    bio = (partner.bio or "").strip()
    if len(bio) > 400:
        bio = bio[:399] + "…"
    caption = labels["caption"].format(
        anon=partner.anon_id,
        age=partner.age,
        gender=labels["gender_m"] if partner.gender == "male" else labels["gender_f"],
        bio=bio,
    )
    return caption.rstrip()


async def send_preview_card(target, notification, lang):
    """Карточка для outbox-воркера: фото партнёра (или текст без него) и
    кнопки. Ошибка стекает к штатному обработчику retry/failed. Оффер,
    закрытый до доставки, честно отвечает «недоступно» вместо пустого
    сообщения."""
    offer = None
    partner = None
    async with async_session() as s:
        if notification.callback_data and notification.callback_data.isdigit():
            offer = await s.get(PreviewOffer, int(notification.callback_data))
            if offer:
                partner_id = (
                    offer.user2_id
                    if offer.user1_id == notification.user_id
                    else offer.user1_id
                )
                partner = (
                    await s.scalars(select(User).where(User.telegram_id == partner_id))
                ).first()
    labels = T.tr(T.PREVIEW_TEXTS, lang)
    if partner is None:
        await target.send_message(notification.user_id, labels["gone"])
        return
    keyboard = preview_request_keyboard(offer.id, lang)
    if partner.profile_photo:
        from utils.photos import send_photo

        await send_photo(
            target,
            notification.user_id,
            partner.profile_photo,
            caption=preview_caption(partner, lang),
            reply_markup=keyboard,
        )
    else:
        await target.send_message(
            notification.user_id,
            preview_caption(partner, lang),
            reply_markup=keyboard,
        )


@preview_router.callback_query(F.data.startswith("pv:"))
async def preview_decision(call: CallbackQuery):
    if not call.message:
        return
    uid = call.from_user.id
    lang = await user_lang(uid, call.from_user.language_code or "ru")
    labels = T.tr(T.PREVIEW_TEXTS, lang)
    parts = call.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await call.answer(labels["gone"])
        return
    action, offer_id = parts[1], int(parts[2])
    from services.preview import PreviewService

    if action == "yes":
        state, chat = await PreviewService.consent(uid, offer_id)
        if state == "mutual":
            from handlers.search import cancel_task
            from services.chats import ChatService

            await ChatService().sync(chat)
            for party in (chat.user1_id, chat.user2_id):
                await cancel_task(party)
            await call.answer(labels["matched"])
        elif state == "waited":
            await call.answer(labels["waited"])
        else:
            await call.answer(labels["gone"])
    elif action == "no":
        state = await PreviewService.decline(uid, offer_id)
        await call.answer(labels["declined"] if state != "gone" else labels["gone"])
    else:
        await call.answer(labels["gone"])
