"""User-facing friends and "return the partner" buttons."""

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from handlers import community_router
from keyboards import texts as T
from keyboards.community import friends_kb
from services.community import FriendService, ReconnectService

RESULT_KEYS = {
    "SENT": "request_sent",
    "ACCEPTED": "accepted",
    "ALREADY_PENDING": "already",
    "ALREADY_FRIENDS": "friends",
    "BLOCKED": "blocked",
    "COOLDOWN": "cooldown",
    "LIMIT": "limit",
    "NOT_FOUND": "not_found",
    "ALREADY": "not_found",
    "NOT_ADDRESSEE": "not_found",
    "EXPIRED": "not_found",
    "DECLINED": "declined",
    "NOT_FRIEND": "not_friend",
    "BUSY": "busy",
    "INVITED": "invite_sent",
    "JOINED": "joined",
    "WAITING": "reconnect_waiting",
    "MATCHED": "reconnect_done",
    "ALREADY_MATCHED": "reconnect_already",
}


def _int(value):
    return int(value) if value.isascii() and value.isdigit() else None


async def _answer(callback, result, lang, *, alert=False):
    words = T.tr(T.FRIEND_TEXT, lang)
    key = RESULT_KEYS.get(result.code, "not_found")
    await callback.answer(words.get(key, words["not_found"]), show_alert=alert)
    return key


@community_router.callback_query(F.data.startswith("friend:req:"))
async def cb_friend_request(callback: CallbackQuery, lang: str = "ru"):
    session_id = callback.data.removeprefix("friend:req:")
    result = await FriendService().request(callback.from_user.id, session_id)
    await _answer(callback, result, lang, alert=not result.ok)


@community_router.callback_query(F.data.startswith("friend:ok:"))
async def cb_friend_accept(callback: CallbackQuery, lang: str = "ru"):
    friendship_id = _int(callback.data.removeprefix("friend:ok:"))
    result = (
        await FriendService().respond(callback.from_user.id, friendship_id, True)
        if friendship_id is not None
        else None
    )
    if result is None:
        await callback.answer(T.tr(T.FRIEND_TEXT, lang)["not_found"])
        return
    await _answer(callback, result, lang, alert=not result.ok)
    if result.ok:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@community_router.callback_query(F.data.startswith("friend:no:"))
async def cb_friend_decline(callback: CallbackQuery, lang: str = "ru"):
    friendship_id = _int(callback.data.removeprefix("friend:no:"))
    result = (
        await FriendService().respond(callback.from_user.id, friendship_id, False)
        if friendship_id is not None
        else None
    )
    if result is None:
        await callback.answer(T.tr(T.FRIEND_TEXT, lang)["not_found"])
        return
    await _answer(callback, result, lang)


@community_router.callback_query(F.data == "profile:friends")
async def cb_friends(callback: CallbackQuery, lang: str = "ru"):
    rows = await FriendService().list(callback.from_user.id)
    words = T.tr(T.FRIEND_TEXT, lang)
    text = (
        words["title"]
        + "\n\n"
        + (
            "\n".join(words["row"].format(anon=anon) for _fid, anon in rows)
            if rows
            else words["empty"]
        )
    )
    await callback.answer()
    await callback.message.answer(text, reply_markup=friends_kb(rows, lang))


@community_router.callback_query(F.data.startswith("friend:call:"))
async def cb_friend_invite(callback: CallbackQuery, lang: str = "ru"):
    friendship_id = _int(callback.data.removeprefix("friend:call:"))
    result = (
        await FriendService().invite(callback.from_user.id, friendship_id)
        if friendship_id is not None
        else None
    )
    if result is None:
        await callback.answer(T.tr(T.FRIEND_TEXT, lang)["not_found"])
        return
    await _answer(callback, result, lang, alert=not result.ok)


@community_router.callback_query(F.data.startswith("friend:join:"))
async def cb_friend_join(callback: CallbackQuery, lang: str = "ru"):
    friendship_id = _int(callback.data.removeprefix("friend:join:"))
    result = (
        await FriendService().join(callback.from_user.id, friendship_id)
        if friendship_id is not None
        else None
    )
    if result is None:
        await callback.answer(T.tr(T.FRIEND_TEXT, lang)["not_found"])
        return
    await _answer(callback, result, lang, alert=not result.ok)


@community_router.callback_query(F.data.startswith("friend:decline:"))
async def cb_friend_invite_decline(callback: CallbackQuery, lang: str = "ru"):
    friendship_id = _int(callback.data.removeprefix("friend:decline:"))
    if friendship_id is None:
        await callback.answer(T.tr(T.FRIEND_TEXT, lang)["not_found"])
        return
    await callback.answer(T.tr(T.FRIEND_TEXT, lang)["declined_toast"])
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@community_router.callback_query(F.data.startswith("reconnect:"))
async def cb_reconnect(callback: CallbackQuery, lang: str = "ru"):
    session_id = callback.data.removeprefix("reconnect:")
    result = await ReconnectService().press(callback.from_user.id, session_id)
    await _answer(
        callback, result, lang, alert=result.code in {"EXPIRED", "BLOCKED", "BUSY"}
    )
