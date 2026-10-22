from html import escape
from aiogram import F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramBadRequest,
)
from handlers import reveal_router
from services.chats import ChatService
from services.reveal import RevealService
from keyboards import texts as T
from utils.db import user_lang
from utils import bots, photos


def reveal_keyboard(sid, lang="ru"):
    buttons = T.tr(T.REVEAL_BUTTONS, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=buttons["offer"],
                    icon_custom_emoji_id="5348232622898167572",
                    callback_data=f"v1:reveal:{sid}",
                    style="primary",
                )
            ]
        ]
    )


def reveal_request_keyboard(sid, lang="ru"):
    buttons = T.tr(T.REVEAL_BUTTONS, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=buttons["agree"],
                    callback_data=f"v1:reveal:{sid}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=buttons["decline"],
                    callback_data=f"v1:reveal:no:{sid}",
                    style="danger",
                ),
            ]
        ]
    )


async def show_reveal(message, user_id, lang="ru"):
    text = T.tr(T.REVEAL_TEXT, lang)
    chat = await ChatService().active(user_id)
    if not chat:
        await message.answer(text["not_in_chat"])
        return
    await message.answer(
        text["intro"], reply_markup=reveal_keyboard(chat.session_id, lang)
    )
    from handlers.premium import product_keyboard
    from utils.db import is_premium

    if not await is_premium(user_id):
        await message.answer(
            text["extras"],
            reply_markup=await product_keyboard(
                ["reveal_priority_request", "reveal_status"], chat.session_id
            ),
        )
    else:
        buttons = T.tr(T.REVEAL_BUTTONS, lang)
        await message.answer(
            text["included"],
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=buttons["priority"],
                            icon_custom_emoji_id="5350618807943576963",
                            callback_data=f"v1:rp:{chat.session_id}",
                        ),
                        InlineKeyboardButton(
                            text=buttons["status"],
                            icon_custom_emoji_id="5350427505805238170",
                            callback_data=f"v1:rs:{chat.session_id}",
                        ),
                    ]
                ]
            ),
        )


@reveal_router.callback_query(F.data.startswith("v1:rp:"))
@reveal_router.callback_query(F.data.startswith("v1:rs:"))
async def cb_reveal_upgrade(callback: CallbackQuery, lang: str = "ru"):
    parts = callback.data.split(":")
    await callback.answer()
    await paid_reveal(
        callback.message,
        callback.from_user.id,
        parts[-1],
        "reveal_priority_request" if parts[1] == "rp" else "reveal_status",
        lang,
    )


@reveal_router.message(F.text.in_(T.all_labels(T.REVEAL)))
async def cmd_reveal(message: Message, lang: str = "ru"):
    await show_reveal(message, message.from_user.id, lang)


@reveal_router.callback_query(
    F.data.in_({"chat:reveal", "reveal:pay", "reveal:mutual", "reveal:check"})
)
async def cb_old_reveal(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await show_reveal(callback.message, callback.from_user.id, lang)


@reveal_router.callback_query(F.data.startswith("v1:reveal:"))
async def cb_consent(callback: CallbackQuery, lang: str = "ru"):
    text = T.tr(T.REVEAL_TEXT, lang)
    if callback.data.startswith("v1:reveal:no:"):
        result = await RevealService().decline(
            callback.from_user.id, callback.data.removeprefix("v1:reveal:no:")
        )
        await callback.answer(
            text["decline_sent"] if result.ok else text["request_gone"]
        )
        if result.ok and callback.message:
            try:
                await callback.message.edit_text(text["declined"])
            except TelegramBadRequest:
                pass  # карточка старше 48 часов — тост уже подтвердил отказ
        return
    sid = callback.data.split(":")[-1]
    result = await RevealService().consent(callback.from_user.id, sid)
    await callback.answer()
    if not result.ok:
        await callback.message.answer(text["already"])
        return
    if result.code == "MUTUAL":
        profiles = result.data["profiles"]
        for uid in profiles:
            other = next((value for key, value in profiles.items() if key != uid), {})
            # Each side is told in their own language, with the other side's
            # contact in the sentence -- the caption is not shared, only built.
            theirs = T.tr(T.REVEAL_TEXT, await user_lang(uid))
            username = other.get("username")
            contact = f"@{escape(username)}" if username else theirs["no_contact"]
            caption = theirs["mutual"].format(contact=contact)
            try:
                # Каждая сторона уведомляется ботом, которым она пользуется:
                # для кросс-бот пары это не тот токен, в котором сработало
                # раскрытие, и чужой бот просто не сможет ей написать.
                target = await bots.bot_for(uid, callback.bot)
                if other.get("photo"):
                    # file_id принадлежит боту, загрузившему фото; photos
                    # поднимает его, если это другой бот.
                    await photos.send_photo(
                        target, uid, other["photo"], caption=caption
                    )
                else:
                    await target.send_message(uid, caption)
            except TelegramForbiddenError:
                pass
            except (TelegramRetryAfter, TelegramBadRequest):
                # A transient send failure here (rate limit, oversized photo, ...)
                # must not silently lose the only notice of a mutual reveal the
                # other side gets. Fall back to the durable outbox (text only,
                # like the initial reveal request already does).
                from services.notifications import enqueue
                from models.base import async_session

                async with async_session.begin() as s:
                    await enqueue(
                        s, uid, "reveal_mutual", f"reveal_mutual:{sid}:{uid}", caption
                    )
    else:
        await callback.message.answer(text["sent"])


async def paid_reveal(message, user_id, session_id, kind, lang="ru"):
    # Consent is still required. Payment only upgrades the request/status view.
    from services.reveal import upgrade_request

    text = T.tr(T.REVEAL_TEXT, lang)
    result = await upgrade_request(user_id, session_id, kind)
    if not result.ok:
        await message.answer(
            text["need_paid"] if result.code == "NOT_PAID" else text["ended"]
        )
        return
    if kind == "reveal_status":
        await message.answer(text.get(f"status_{result.code}", text["status_default"]))
    else:
        await message.answer(
            text["priority_ready"], reply_markup=reveal_keyboard(session_id, lang)
        )
