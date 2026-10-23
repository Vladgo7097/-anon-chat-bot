"""Пользовательский сценарий «Предложить идею»: кнопка меню, черновик в FSM.

Черновик живёт только в FSM-состоянии: пока автор не подтвердил отправку,
в базе нет ни одной строки. Повторные нажатия устаревших кнопок читают
состояние, а не говорят от его имени.
"""

import html

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import config
from handlers import ideas_router
from keyboards import texts as T
from keyboards.inline import (
    empty_ideas_list_kb,
    idea_card_kb,
    idea_draft_kb,
    idea_preview_kb,
    ideas_list_kb,
    ideas_menu_kb,
)
from services.chat_manager import set_online
from services.ideas import IdeaService, status_label
from utils.time import KYIV, utcnow

NOT_FOUND = {
    "ru": "Идея не найдена.",
    "en": "Idea not found.",
    "uk": "Ідею не знайдено.",
}


class IdeaFlow(StatesGroup):
    TEXT = State()
    PREVIEW = State()


DRAFT_KEY = "idea_draft"


def _kyiv(when) -> str:
    return (when or utcnow()).astimezone(KYIV).strftime("%d.%m.%Y %H:%M")


def _short(text: str, size: int = 48) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= size else line[: size - 1] + "…"


async def _root(message, lang: str):
    await message.answer(T.tr(T.IDEA_MENU, lang), reply_markup=ideas_menu_kb(lang))


@ideas_router.message(F.text.in_(T.all_labels(T.IDEA)))
async def open_ideas(message: Message, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await set_online(message.from_user.id)
    await _root(message, lang)


@ideas_router.callback_query(F.data == "idea")
async def cb_root(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        T.tr(T.IDEA_MENU, lang), reply_markup=ideas_menu_kb(lang)
    )


@ideas_router.callback_query(F.data == "idea:new")
async def cb_new(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await state.set_state(IdeaFlow.TEXT)
    await state.set_data({DRAFT_KEY: None})
    await callback.answer()
    await callback.message.answer(
        T.tr(T.IDEA_ASK, lang), reply_markup=idea_draft_kb(lang)
    )


def _validate(draft: str):
    """Ошибки формата: None - ок, иначе (ключ текстов, данные для формата)."""
    if not draft:
        return T.IDEA_EMPTY, {}
    if len(draft) > config.IDEA_MAX_LEN:
        return T.IDEA_TOO_LONG, {"len": len(draft), "max": config.IDEA_MAX_LEN}
    return None


@ideas_router.message(IdeaFlow.TEXT, F.text)
async def idea_text(message: Message, state: FSMContext, lang: str = "ru"):
    draft = (message.text or "").strip()
    invalid = _validate(draft)
    if invalid:
        texts, fmt = invalid
        await message.answer(T.tr(texts, lang).format(**fmt))
        return
    await state.update_data({DRAFT_KEY: draft})
    await state.set_state(IdeaFlow.PREVIEW)
    # Пользовательский текст в HTML-режиме только через escape: кавычки и
    # теги в идее не должны ломать разметку сообщения.
    await message.answer(
        T.tr(T.IDEA_PREVIEW, lang).format(text=html.escape(draft)),
        reply_markup=idea_preview_kb(lang),
    )


@ideas_router.message(IdeaFlow.PREVIEW, F.text)
async def idea_text_again(message: Message, state: FSMContext, lang: str = "ru"):
    """Прямой ввод текста вместо кнопки «Изменить» - тот же цикл правки."""
    draft = (message.text or "").strip()
    invalid = _validate(draft)
    if invalid:
        texts, fmt = invalid
        await message.answer(T.tr(texts, lang).format(**fmt))
        return
    await state.update_data({DRAFT_KEY: draft})
    await message.answer(
        T.tr(T.IDEA_PREVIEW, lang).format(text=html.escape(draft)),
        reply_markup=idea_preview_kb(lang),
    )


@ideas_router.message(
    StateFilter(IdeaFlow.TEXT, IdeaFlow.PREVIEW),
    F.photo
    | F.sticker
    | F.voice
    | F.video
    | F.video_note
    | F.document
    | F.audio
    | F.animation,
)
async def idea_not_text(message: Message, state: FSMContext, lang: str = "ru"):
    # Фильтр держит state: вложение вне сценария идёт другим роутерам.
    await message.answer(T.tr(T.IDEA_NOT_TEXT, lang))


@ideas_router.callback_query(F.data == "idea:edit")
async def cb_edit(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    await state.set_state(IdeaFlow.TEXT)
    await callback.answer()
    await callback.message.answer(
        T.tr(T.IDEA_EDIT_ASK, lang), reply_markup=idea_draft_kb(lang)
    )


@ideas_router.callback_query(F.data == "idea:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await callback.answer()
    await callback.message.answer(T.tr(T.IDEA_CANCELLED, lang))


@ideas_router.callback_query(F.data == "idea:submit")
async def cb_submit(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    data = await state.get_data()
    draft = data.get(DRAFT_KEY)
    if not draft:
        await callback.answer(T.tr(T.IDEA_CALL_DONE, lang))
        return
    author_name = callback.from_user.first_name or ""
    bot_id = callback.message.bot.id if callback.message is not None else None
    result = await IdeaService().create(
        callback.from_user.id, draft, bot_id, author_name
    )
    if not result.ok:
        await callback.answer()
        await callback.message.answer(_limit_text(result, lang))
        return
    # Строка и уведомление владельцу записаны одной транзакцией - теперь
    # состояние можно снять: старая кнопка Отправить больше не живёт.
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        T.tr(T.IDEA_SUCCESS, lang).format(id=result.data["id"]),
        reply_markup=ideas_menu_kb(lang),
    )


def _limit_text(result, lang: str) -> str:
    if result.code == "TOO_LONG":
        return T.tr(T.IDEA_TOO_LONG, lang).format(
            len=result.data["len"], max=result.data["max"]
        )
    if result.code == "COOLDOWN":
        return T.tr(T.IDEA_COOLDOWN, lang).format(min=result.data["min"])
    if result.code == "RATE_LIMITED":
        when = _kyiv(result.data["when"])
        return T.tr(T.IDEA_LIMIT_DAY, lang).format(max=result.data["max"], when=when)
    if result.code == "DUPLICATE_TEXT":
        return T.tr(T.IDEA_DUP_TEXT, lang)
    return T.tr(T.IDEA_EMPTY, lang)


@ideas_router.callback_query(F.data == "idea:mine")
async def cb_mine(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    await _show_mine(callback, 1, lang)


@ideas_router.callback_query(F.data.regexp(r"^idea:mine:(\d+)$"))
async def cb_mine_page(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    page = int(callback.data.split(":")[2])
    await _show_mine(callback, page, lang)


async def _show_mine(callback: CallbackQuery, page: int, lang: str):
    rows, page, pages = await IdeaService().mine(callback.from_user.id, page)
    await callback.answer()
    if not rows:
        await callback.message.answer(
            T.tr(T.IDEA_MINE_EMPTY, lang), reply_markup=empty_ideas_list_kb(lang)
        )
        return
    body = "\n\n".join(
        f"<b>#{row.id}</b> — {html.escape(_short(row.text))}\n{status_label(row.status, lang)}"
        for row in rows
    )
    await callback.message.answer(
        T.tr(T.IDEA_MINE_HEADER, lang).format(list=body),
        reply_markup=ideas_list_kb(lang, page, pages),
    )


@ideas_router.callback_query(F.data.regexp(r"^idea:view:(\d+)$"))
async def cb_view(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    idea_id = int(callback.data.split(":")[2])
    own = await IdeaService().own(idea_id, callback.from_user.id)
    await callback.answer()
    if own is None:
        # Чужой id или устаревшая карточка: одну фразу, без чужих данных.
        await callback.message.answer(T.tr(NOT_FOUND, lang))
        return
    reply = ""
    if own.admin_reply:
        reply = T.tr(T.IDEA_CARD_REPLY, lang).format(
            date=_kyiv(own.admin_reply_at), reply=html.escape(own.admin_reply)
        )
    await callback.message.answer(
        T.tr(T.IDEA_CARD, lang).format(
            id=own.id,
            status=status_label(own.status, lang),
            text=html.escape(own.text),
            created=_kyiv(own.created_at),
            updated=_kyiv(own.updated_at),
            reply=reply,
        ),
        reply_markup=idea_card_kb(lang, own=True),
    )
