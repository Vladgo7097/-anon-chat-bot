"""ОП у пользователя и панель «Обязательная подписка» у владельца.

Пользовательская ветка обязана доходить до роутера даже при закрытом гейте:
AccessMiddleware пропускает op:* без проверки подписки -- иначе кнопка
«Проверить подписку» не смогла бы ничего перепроверить. Результат проверки
обновляется в том же сообщении, а не новым (ТЗ: не засорять диалог).
"""

import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from handlers import op_admin_router, op_router
from handlers.admin import AdminOnly
from services import op as op_service
from utils import bots
from utils.time import KYIV, utcnow

logger = logging.getLogger(__name__)

# Та же защита, что у /admin: панель открыта только ADMIN_IDS.
op_admin_router.message.middleware(AdminOnly())
op_admin_router.callback_query.middleware(AdminOnly())


class AdminOpTerm(StatesGroup):
    VALUE = State()


class AdminOpText(StatesGroup):
    VALUE = State()


class AdminOpChat(StatesGroup):
    REF = State()


TERM_HINT = (
    "Пришли дату и время окончания по Киеву.\n"
    "Форматы: 18.10.2026 23:00, 18.10 23:00 или 23:00 (сегодня).\n"
    "/cancel — отмена."
)
CHAT_HINT = (
    "Пришли @username, ссылку t.me/... или ID чата (например -1001234567890).\n"
    "Бот должен быть там администратором: только тогда Telegram отдаёт чужие подписки.\n"
    "/cancel — отмена."
)
TEXT_HINT = (
    "Пришли текст требования обычным текстом, до 3500 символов. Список недостающих "
    "подписок и кнопки бот добавит сам.\n/cancel — отмена."
)

OP_ACTION_TITLES = {
    "op_enable": "ОП включена",
    "op_disable": "ОП выключена",
    "op_auto_disable": "Автоотключение по сроку",
    "op_term": "Изменён срок",
    "op_text": "Изменён текст",
    "op_chat_add": "Ресурс добавлен",
    "op_chat_del": "Ресурс удалён",
    "op_chat_toggle": "Ресурс вкл/выкл",
    "op_chat_verify": "Проверка прав",
    "op_notify_start": "Рассылка запущена",
    "op_notify_stop": "Рассылка остановлена",
    "op_notify_done": "Рассылка завершена",
}

KIND_TITLES = {"channel": "канал", "group": "группа", "supergroup": "супергруппа"}


async def _show(callback: CallbackQuery, text: str, markup=None) -> None:
    message = callback.message
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    from handlers.start import callback_target

    await callback_target(callback).answer(text, reply_markup=markup)


async def _prompt(callback: CallbackQuery, text: str) -> None:
    from handlers.start import callback_target

    await callback_target(callback).answer(text)


async def _render_main(callback: CallbackQuery) -> None:
    settings, chats, _ = await op_service.panel_state(callback.bot.id)
    await _show(
        callback, _main_text(settings, chats, callback.bot.id), _main_kb(settings)
    )


def _state_line(settings) -> str:
    if settings is None or not settings.enabled:
        return "Состояние: выключена"
    if op_service.is_active(settings):
        if settings.ends_at:
            return (
                f"Состояние: включена до {op_service.fmt_when(settings.ends_at)} (Киев)"
            )
        return "Состояние: включена без срока"
    return f"Состояние: ждёт старта с {op_service.fmt_when(settings.starts_at)} (Киев)"


def _main_text(settings, chats, bot_id) -> str:
    lines = [
        f"📛 Обязательная подписка — {bots.label(bot_id)}",
        "",
        _state_line(settings),
    ]
    active = sum(1 for c in chats if c.active)
    broken = sum(1 for c in chats if c.active and not c.rights_ok)
    suffix = f", без прав {broken}" if broken else ""
    lines.append(f"Ресурсы: {len(chats)} (активных {active}{suffix})")
    if settings is not None:
        lines.append(
            f"Проверки: {settings.checks_total} "
            f"(не прошли {settings.checks_blocked}, ошибки {settings.checks_error})"
        )
        if settings.notify_running:
            lines.append(
                f"Рассылка: идёт, {settings.notify_done}/{settings.notify_total}, "
                f"уведомлений {settings.notify_sent}"
            )
        elif settings.notify_sent:
            lines.append(f"Рассылка: последняя — {settings.notify_sent} уведомлений")
    if broken:
        lines += [
            "",
            "⚠️ Активный ресурс без прав администратора: проверка у людей будет падать "
            "с ошибкой, и доступ не откроется. Выдай права и нажми «Проверить права».",
        ]
    if any(c.active and not c.link for c in chats):
        lines += [
            "",
            "⚠️ У части активных ресурсов нет ссылки — в требовании останется только название.",
        ]
    return "\n".join(lines)


def _main_kb(settings) -> InlineKeyboardMarkup:
    enabled = bool(settings and settings.enabled)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выключить" if enabled else "Включить",
                    callback_data="admin:op:toggle",
                    style="danger" if enabled else "success",
                ),
                InlineKeyboardButton(text="📅 Срок", callback_data="admin:op:term"),
            ],
            [
                InlineKeyboardButton(
                    text="Каналы и группы", callback_data="admin:op:chats"
                ),
                InlineKeyboardButton(text="✏️ Текст", callback_data="admin:op:text"),
            ],
            [
                InlineKeyboardButton(
                    text="📨 Уведомить неподписанных", callback_data="admin:op:notify"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Проверки и журнал", callback_data="admin:op:log"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5258236805890710909",
                    callback_data="admin:sec:op",
                )
            ],
        ]
    )


def _term_text(settings) -> str:
    lines = ["📅 Срок действия ОП", ""]
    if settings is not None and settings.starts_at is not None:
        lines.append(f"Старт: {op_service.fmt_when(settings.starts_at)} (Киев)")
    else:
        lines.append("Старт: сразу после включения")
    if settings is not None and settings.ends_at is not None:
        lines.append(f"Окончание: {op_service.fmt_when(settings.ends_at)} (Киев)")
    else:
        lines.append("Окончание: не задано — работает до ручного выключения")
    if settings is None or not settings.enabled:
        lines += [
            "",
            "ОП выключена: срок сохранится, но действовать начнёт после включения.",
        ]
    return "\n".join(lines)


def _term_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2 часа", callback_data="admin:op:term:2"),
                InlineKeyboardButton(text="24 часа", callback_data="admin:op:term:24"),
                InlineKeyboardButton(text="7 дней", callback_data="admin:op:term:168"),
            ],
            [
                InlineKeyboardButton(
                    text="Своя дата", callback_data="admin:op:term:custom"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Снять срок", callback_data="admin:op:term:clear"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5258236805890710909",
                    callback_data="admin:op:main",
                )
            ],
        ]
    )


def _chats_text(chats) -> str:
    if not chats:
        return (
            "📛 Каналы и группы\n\nПусто. Добавь @username или ID — бот проверит права и "
            "включит ресурс, только если он администратор."
        )
    active = sum(1 for c in chats if c.active)
    lines = ["📛 Каналы и группы", "", f"Всего {len(chats)}, активных {active}."]
    for c in chats:
        mark = "🟢" if (c.active and c.rights_ok) else ("⚠️" if not c.rights_ok else "⏸")
        lines.append(f"{mark} {c.title or c.chat_id}")
    return "\n".join(lines)


def _chats_kb(chats) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        mark = "🟢" if (c.active and c.rights_ok) else ("⚠️" if not c.rights_ok else "⏸")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {(c.title or str(c.chat_id))[:48]}",
                    callback_data=f"admin:op:chat:{c.chat_id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="➕ Добавить", callback_data="admin:op:add")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Проверить права у всех", callback_data="admin:op:verify"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                icon_custom_emoji_id="5258236805890710909",
                callback_data="admin:op:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_card_text(row) -> str:
    lines = [
        f"📛 {row.title or row.chat_id}",
        "",
        f"Тип: {KIND_TITLES.get(row.kind, row.kind or '—')}",
        f"ID: {row.chat_id}",
        f"Ссылка: {row.link or '—'}",
        f"Права бота: {'✅ есть' if row.rights_ok else '⚠️ нет (' + (row.rights_error or 'неизвестно') + ')'}",
        f"В списке: {'включён' if row.active else 'выключен'}",
    ]
    if row.verified_at:
        lines.append(f"Проверен: {op_service.fmt_when(row.verified_at)}")
    if not row.rights_ok:
        lines += [
            "",
            "Без прав администратора Telegram не отдаёт чужие подписки — ресурс нельзя включить.",
        ]
    return "\n".join(lines)


def _chat_card_kb(row) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Проверить права",
                    callback_data=f"admin:op:chat:{row.chat_id}:verify",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Выключить" if row.active else "Включить",
                    callback_data=f"admin:op:chat:{row.chat_id}:toggle",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Удалить",
                    style="danger",
                    callback_data=f"admin:op:chat:{row.chat_id}:del",
                )
            ],
            [InlineKeyboardButton(text="К списку", callback_data="admin:op:chats")],
        ]
    )


def _chat_confirm_kb(chat_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    style="danger",
                    callback_data=f"admin:op:chat:{chat_id}:del_yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"admin:op:chat:{chat_id}"
                )
            ],
        ]
    )


def _text_screen(settings) -> str:
    current = settings.text if settings is not None and settings.text else None
    lines = ["✏️ Текст требования", ""]
    if current:
        lines.append(current)
    else:
        lines.append(
            "Сейчас стандартный текст — на языке пользователя (ru/en/uk), список недостающих "
            "подписок и кнопки бот добавляет сам."
        )
    return "\n".join(lines)


def _text_kb(has_custom: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="admin:op:text:edit")]
    ]
    if has_custom:
        rows.append(
            [
                InlineKeyboardButton(
                    text="♻️ Вернуть стандартный", callback_data="admin:op:text:clear"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                icon_custom_emoji_id="5258236805890710909",
                callback_data="admin:op:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _notify_text(settings, chats) -> str:
    active_chats = [c for c in chats if c.active and c.rights_ok]
    lines = ["📨 Уведомление неподписанных", ""]
    if settings is not None and settings.notify_running:
        lines.append(
            f"Идёт: обработано {settings.notify_done} из {settings.notify_total}, "
            f"уведомлений {settings.notify_sent}."
        )
    else:
        lines.append(
            "Бот по очереди проверит подписки известных ему пользователей и отправит требование "
            "тем, кому не хватает ресурсов. Повторный запуск не создаёт дубликатов; написать "
            "можно только тем, кто не блокировал бота."
        )
    if not active_chats:
        lines += ["", "⚠️ Нет активных ресурсов с правами — уведомлять не о чем."]
    elif not op_service.is_active(settings):
        lines += ["", "⚠️ ОП сейчас не действует — включи её, иначе требовать нечего."]
    return "\n".join(lines)


def _notify_kb(settings, chats) -> InlineKeyboardMarkup:
    rows = []
    if settings is not None and settings.notify_running:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏹ Остановить",
                    style="danger",
                    callback_data="admin:op:notify:stop",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚀 Запустить",
                    style="success",
                    callback_data="admin:op:notify:start",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:op:notify")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                icon_custom_emoji_id="5258236805890710909",
                callback_data="admin:op:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _log_text(settings, chats, log) -> str:
    lines = ["📜 Проверки и журнал"]
    if settings is not None:
        lines += [
            "",
            f"Проверок: {settings.checks_total}",
            f"Не прошли: {settings.checks_blocked}",
            f"Ошибки проверки: {settings.checks_error}",
        ]
    broken = [c for c in chats if not c.rights_ok]
    if broken:
        lines += ["", "Ресурсы без прав:"]
        lines += [f"⚠️ {c.title or c.chat_id}" for c in broken]
    if log:
        lines += ["", "Последние события:"]
        for row in log:
            when = (
                row.created_at.astimezone(KYIV).strftime("%d.%m %H:%M")
                if row.created_at
                else "—"
            )
            lines.append(f"{when} · {OP_ACTION_TITLES.get(row.action, row.action)}")
    return "\n".join(lines)


def _log_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:op:log")],
            [
                InlineKeyboardButton(
                    text="Назад",
                    icon_custom_emoji_id="5258236805890710909",
                    callback_data="admin:op:main",
                )
            ],
        ]
    )


# ── Пользовательская часть ───────────────────────────────────────


@op_router.callback_query(F.data == "op:check")
async def cb_op_check(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    gate = await op_service.op_gate(callback.bot, callback.from_user.id, force=True)
    await callback.answer()
    from handlers.start import callback_target, enter_bot

    target = callback_target(callback)
    if not gate.blocked:
        text = (
            op_service.tr(op_service.OK_TEXT, lang)
            if gate.required
            else op_service.tr(op_service.OFF_TEXT, lang)
        )
        message = callback.message
        if message is not None:
            try:
                await message.edit_text(text)
            except TelegramBadRequest:
                await target.answer(text)
        else:
            await target.answer(text)
        await enter_bot(target, state, callback.from_user, callback.bot)
        return
    await _edit_requirement(callback, gate, lang)


async def _edit_requirement(callback: CallbackQuery, gate, lang: str) -> None:
    text = op_service.op_screen_text(gate, lang)
    markup = op_service.op_screen_kb(gate, lang)
    message = callback.message
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    from handlers.start import callback_target

    await callback_target(callback).answer(text, reply_markup=markup)


# ── Панель ───────────────────────────────────────────────────────


@op_admin_router.callback_query(F.data == "admin:op:main")
async def cb_op_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _render_main(callback)


@op_admin_router.callback_query(F.data == "admin:op:toggle")
async def cb_op_toggle(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    bot_id = callback.bot.id
    now = utcnow()
    async with op_service.async_session.begin() as s:
        settings = await op_service.settings_or_create(s, bot_id)
        before = settings.enabled
        settings.enabled = not settings.enabled
        if settings.enabled:
            if settings.starts_at is None or settings.starts_at > now:
                settings.starts_at = now
            if settings.ends_at is not None and settings.ends_at <= now:
                settings.ends_at = None
            settings.campaign_id += 1
        settings.updated_at = now
        from services.admin import audit

        audit(
            s,
            callback.from_user.id,
            "op_enable" if settings.enabled else "op_disable",
            "op",
            bot_id,
            before={"enabled": before},
            after={"enabled": settings.enabled},
        )
        enabled = settings.enabled
    await callback.answer("Включена" if enabled else "Выключена")
    await _render_main(callback)


@op_admin_router.callback_query(F.data == "admin:op:term")
async def cb_op_term(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    settings, _, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _term_text(settings), _term_kb())


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:term:(2|24|168)$"))
async def cb_op_term_preset(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    hours = int(callback.data.rsplit(":", 1)[1])
    from services.admin import audit

    async with op_service.async_session.begin() as s:
        settings = await op_service.settings_or_create(s, callback.bot.id)
        op_service.apply_duration(settings, hours)
        settings.updated_at = utcnow()
        audit(
            s,
            callback.from_user.id,
            "op_term",
            "op",
            callback.bot.id,
            after={"hours": hours, "ends_at": settings.ends_at.isoformat()},
        )
    await callback.answer(f"Срок: {hours} ч.")
    settings, _, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _term_text(settings), _term_kb())


@op_admin_router.callback_query(F.data == "admin:op:term:clear")
async def cb_op_term_clear(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from services.admin import audit

    async with op_service.async_session.begin() as s:
        settings = await op_service.settings_or_create(s, callback.bot.id)
        settings.starts_at = None
        settings.ends_at = None
        settings.updated_at = utcnow()
        audit(
            s,
            callback.from_user.id,
            "op_term",
            "op",
            callback.bot.id,
            after={"cleared": True},
        )
    await callback.answer("Срок снят")
    settings, _, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _term_text(settings), _term_kb())


@op_admin_router.callback_query(F.data == "admin:op:term:custom")
async def cb_op_term_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminOpTerm.VALUE)
    await _prompt(callback, TERM_HINT)


@op_admin_router.message(AdminOpTerm.VALUE)
async def op_term_input(message: Message, state: FSMContext):
    try:
        moment = op_service.parse_deadline(message.text or "")
    except ValueError:
        await message.answer(
            "Не понял дату. Формат: 18.10.2026 23:00, короче — 18.10 23:00 или 23:00. /cancel — отмена."
        )
        return
    if moment <= utcnow():
        await message.answer("Это время уже прошло — нужно будущее. /cancel — отмена.")
        return
    from services.admin import audit

    async with op_service.async_session.begin() as s:
        settings = await op_service.settings_or_create(s, message.bot.id)
        if settings.starts_at is None:
            settings.starts_at = utcnow()
        settings.ends_at = moment
        settings.updated_at = utcnow()
        audit(
            s,
            message.from_user.id,
            "op_term",
            "op",
            message.bot.id,
            after={"ends_at": moment.isoformat()},
        )
    await state.clear()
    settings, chats, _ = await op_service.panel_state(message.bot.id)
    await message.answer(
        "Срок обновлён.\n\n" + _main_text(settings, chats, message.bot.id),
        reply_markup=_main_kb(settings),
    )


@op_admin_router.callback_query(F.data == "admin:op:chats")
async def cb_op_chats(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    _, chats, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _chats_text(chats), _chats_kb(chats))


@op_admin_router.callback_query(F.data == "admin:op:add")
async def cb_op_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminOpChat.REF)
    await _prompt(callback, CHAT_HINT)


@op_admin_router.message(AdminOpChat.REF)
async def op_chat_input(message: Message, state: FSMContext):
    ok, text = await op_service.add_chat(
        message.bot, message.bot.id, message.text or "", message.from_user.id
    )
    await state.clear()
    _, chats, _ = await op_service.panel_state(message.bot.id)
    await message.answer(
        text + "\n\n" + _chats_text(chats), reply_markup=_chats_kb(chats)
    )


@op_admin_router.callback_query(F.data == "admin:op:verify")
async def cb_op_verify_all(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _, chats, _ = await op_service.panel_state(callback.bot.id)
    fixed = broken = 0
    for row in chats:
        ok, _detail = await op_service.verify_chat(
            callback.bot, callback.bot.id, row.chat_id, callback.from_user.id
        )
        fixed += ok
        broken += not ok
    await callback.answer(f"Права: есть у {fixed}, нет у {broken}", show_alert=True)
    _, chats, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _chats_text(chats), _chats_kb(chats))


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:chat:-?\d+$"))
async def cb_op_chat(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    chat_id = int(callback.data.rsplit(":", 1)[1])
    row = await op_service.chat_row(callback.bot.id, chat_id)
    if row is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    await callback.answer()
    await _show(callback, _chat_card_text(row), _chat_card_kb(row))


@op_admin_router.callback_query(
    F.data.regexp(r"^admin:op:chat:-?\d+:(verify|toggle|del|del_yes)$")
)
async def cb_op_chat_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    action, chat_id = parts[-1], int(parts[-2])
    bot_id = callback.bot.id
    row = await op_service.chat_row(bot_id, chat_id)
    if row is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    if action == "verify":
        ok, text = await op_service.verify_chat(
            callback.bot, bot_id, chat_id, callback.from_user.id
        )
        await callback.answer(text, show_alert=not ok)
        row = await op_service.chat_row(bot_id, chat_id)
        await _show(callback, _chat_card_text(row), _chat_card_kb(row))
        return
    if action == "toggle":
        new_state = not row.active
        if new_state and not row.rights_ok:
            await callback.answer(
                "Сначала проверь права: без них чужие подписки не проверяются.",
                show_alert=True,
            )
            return
        await op_service.set_chat_active(
            bot_id, chat_id, new_state, callback.from_user.id
        )
        await callback.answer("Включён" if new_state else "Выключен")
        row = await op_service.chat_row(bot_id, chat_id)
        await _show(callback, _chat_card_text(row), _chat_card_kb(row))
        return
    if action == "del":
        await callback.answer()
        await _show(
            callback,
            f"Удалить «{row.title or chat_id}»?\n\nРесурс перестанет требоваться у пользователей.",
            _chat_confirm_kb(chat_id),
        )
        return
    removed = await op_service.delete_chat(bot_id, chat_id, callback.from_user.id)
    await callback.answer("Удалён" if removed else "Уже удалён")
    _, chats, _ = await op_service.panel_state(bot_id)
    await _show(callback, _chats_text(chats), _chats_kb(chats))


@op_admin_router.callback_query(F.data == "admin:op:text")
async def cb_op_text(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    settings, _, _ = await op_service.panel_state(callback.bot.id)
    await _show(
        callback, _text_screen(settings), _text_kb(bool(settings and settings.text))
    )


@op_admin_router.callback_query(F.data == "admin:op:text:edit")
async def cb_op_text_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminOpText.VALUE)
    await _prompt(callback, TEXT_HINT)


@op_admin_router.callback_query(F.data == "admin:op:text:clear")
async def cb_op_text_clear(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from services.admin import audit

    async with op_service.async_session.begin() as s:
        settings = await op_service.settings_or_create(s, callback.bot.id)
        settings.text = None
        settings.updated_at = utcnow()
        audit(
            s,
            callback.from_user.id,
            "op_text",
            "op",
            callback.bot.id,
            after={"cleared": True},
        )
    await callback.answer("Вернул стандартный текст")
    settings, _, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _text_screen(settings), _text_kb(False))


@op_admin_router.message(AdminOpText.VALUE)
async def op_text_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст не сохраняю. /cancel — отмена.")
        return
    if len(text) > 3500:
        await message.answer(
            "Слишком длинно: максимум 3500 символов. /cancel — отмена."
        )
        return
    from services.admin import audit

    async with op_service.async_session.begin() as s:
        settings = await op_service.settings_or_create(s, message.bot.id)
        settings.text = text
        settings.updated_at = utcnow()
        audit(
            s,
            message.from_user.id,
            "op_text",
            "op",
            message.bot.id,
            after={"length": len(text)},
        )
    await state.clear()
    settings, chats, _ = await op_service.panel_state(message.bot.id)
    await message.answer(
        _main_text(settings, chats, message.bot.id), reply_markup=_main_kb(settings)
    )


@op_admin_router.callback_query(F.data == "admin:op:notify")
async def cb_op_notify(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    settings, chats, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _notify_text(settings, chats), _notify_kb(settings, chats))


@op_admin_router.callback_query(F.data == "admin:op:notify:start")
async def cb_op_notify_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    settings, chats, _ = await op_service.panel_state(callback.bot.id)
    if not any(c.active and c.rights_ok for c in chats) or not op_service.is_active(
        settings
    ):
        settings, chats, _ = await op_service.panel_state(callback.bot.id)
        await _show(
            callback, _notify_text(settings, chats), _notify_kb(settings, chats)
        )
        return
    await _show(
        callback,
        "Запустить проверку известных пользователей и отправку требования неподписанным?\n\n"
        "Рассылка идёт порциями через общую очередь, повторный запуск дубликатов не создаёт.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, запустить",
                        style="success",
                        callback_data="admin:op:notify:yes",
                    )
                ],
                [InlineKeyboardButton(text="Отмена", callback_data="admin:op:notify")],
            ]
        ),
    )


@op_admin_router.callback_query(F.data == "admin:op:notify:yes")
async def cb_op_notify_yes(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    bot_id = callback.bot.id
    settings, chats, _ = await op_service.panel_state(bot_id)
    if not op_service.is_active(settings):
        await callback.answer("ОП сейчас не действует — включи её.", show_alert=True)
        return
    if not any(c.active and c.rights_ok for c in chats):
        await callback.answer("Нет активных ресурсов с правами.", show_alert=True)
        return
    total = await op_service.request_notify(bot_id, callback.from_user.id)
    await callback.answer(f"Запущено: {total} пользователей")
    settings, chats, _ = await op_service.panel_state(bot_id)
    await _show(callback, _notify_text(settings, chats), _notify_kb(settings, chats))


@op_admin_router.callback_query(F.data == "admin:op:notify:stop")
async def cb_op_notify_stop(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await op_service.stop_notify(callback.bot.id, callback.from_user.id)
    await callback.answer("Остановлена")
    settings, chats, _ = await op_service.panel_state(callback.bot.id)
    await _show(callback, _notify_text(settings, chats), _notify_kb(settings, chats))


@op_admin_router.callback_query(F.data == "admin:op:log")
async def cb_op_log(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    settings, chats, log = await op_service.panel_state(callback.bot.id)
    await _show(callback, _log_text(settings, chats, log), _log_kb())


@op_admin_router.message(
    Command("cancel"),
    StateFilter(AdminOpTerm.VALUE, AdminOpText.VALUE, AdminOpChat.REF),
)
async def op_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")
