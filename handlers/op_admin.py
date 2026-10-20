"""ОП v2: панель управления -- корень, ресурсы, диагностика и аудит.

Тонкий слой: читает данные через services/op_admin.py, собирает текст и
клавиатуру. Любой тап по панели снимает ожидание ввода, иначе старый
ADD/LINK перехватил бы следующий текст администратора.
"""

from __future__ import annotations

import json
import logging
from html import escape

from aiogram import BaseMiddleware, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers import op_admin_router
from handlers.admin import AdminOnly, safe_send
from keyboards.inline import (
    op_admin_kb,
    op_audit_kb,
    op_buttons_kb,
    op_cancel_kb,
    op_confirm_delete_kb,
    op_confirm_reset_kb,
    op_diag_kb,
    op_message_editor_kb,
    op_messages_kb,
    op_resources_kb,
    op_single_kb,
    op_target_kb,
)
from services import op as core
from services import op_admin as panel
from utils import bots
from utils.time import utcnow

logger = logging.getLogger(__name__)


class OpAdminTarget(StatesGroup):
    ADD = State()
    LINK = State()


class OpAdminMessage(StatesGroup):
    TEXT = State()
    BUTTON = State()


class ClearOnTap(BaseMiddleware):
    """Снимает ожидание ввода на любом тапе панели."""

    async def __call__(self, handler, event, data):
        state = data.get("state")
        if state is not None:
            await state.clear()
        return await handler(event, data)


op_admin_router.callback_query.middleware(AdminOnly())
op_admin_router.callback_query.middleware(ClearOnTap())
op_admin_router.message.middleware(AdminOnly())


ADD_PROMPT = (
    "➕ Добавление ресурса\n\n"
    "Перешли любое сообщение из канала или группы — форвард сохранит chat_id, "
    "даже если у чата нет публичного имени.\n\n"
    "Либо отправь:\n"
    "• @username\n"
    "• -1001234567890\n"
    "• https://t.me/username\n\n"
    "Бот должен быть администратором чата, иначе Telegram не отдаст подписки.\n"
    "Отмена — /cancel."
)
LINK_PROMPT = (
    "🔗 Ссылка для вступления\n\n"
    "Отправь ссылку вида https://t.me/... — её увидят пользователи на экране ОП.\n"
    "Отмена — /cancel."
)


def _status_text(settings) -> str:
    if not settings.enabled:
        return "⚪ выключена"
    if settings.paused:
        return "⏸ пауза"
    return "🟢 включена"


def _window_line(starts_at, ends_at) -> str:
    if starts_at is None and ends_at is None:
        return "без ограничения"
    left = core.fmt_when(starts_at) if starts_at else "сразу"
    right = core.fmt_when(ends_at) if ends_at else "бессрочно"
    return f"{left} → {right}"


def _root_text(data) -> str:
    settings = data["settings"]
    targets = data["targets"]
    policies = data["policies"]
    now = utcnow()
    active = sum(
        1
        for target in targets
        if target.is_active and core.window_live(target.starts_at, target.ends_at, now)
    )
    failed = sum(1 for target in targets if target.setup_status == "error")
    globals_count = sum(1 for policy in policies if policy.feature_key == "*")
    features_count = len(policies) - globals_count
    lines = [
        "🧩 Обязательная подписка",
        "",
        f"Статус: {_status_text(settings)}",
        f"Требование сейчас: {'действует' if data['required'] else 'не действует'}",
        f"Окно: {_window_line(settings.starts_at, settings.ends_at)} · {settings.timezone}",
        "",
        f"Ресурсы: {len(targets)} · активных {active} · с ошибкой {failed}",
        f"Правила: {globals_count} глобальных · {features_count} по функциям",
    ]
    if settings.enabled and not targets:
        lines += ["", "⚠️ Ресурсов нет — включённая ОП ничего не блокирует."]
    elif settings.enabled and not policies:
        lines += ["", "⚠️ Правил нет — включённая ОП ничего не блокирует."]
    elif settings.enabled and failed:
        lines += ["", f"⚠️ Ресурсов с ошибкой прав: {failed} — открой диагностику."]
    else:
        lines += [
            "",
            "Проверка идёт на каждое защищённое действие; ошибка Telegram доступ не открывает.",
        ]
    return "\n".join(lines)


def _resources_text(targets) -> str:
    if not targets:
        return (
            "🗂 Ресурсы\n\n"
            "Пока пусто. Добавь канал или группу — без ресурса ОП не действует."
        )
    lines = ["🗂 Ресурсы", ""]
    for index, target in enumerate(targets, 1):
        mark = (
            "✅"
            if target.is_active and target.setup_status == "verified"
            else (
                "⛔"
                if target.setup_status == "error"
                else ("⏸" if not target.is_active else "⚪")
            )
        )
        lines.append(
            f"{mark} {index}. {escape(target.title or str(target.chat_id))} · {target.chat_id}"
        )
    lines += ["", "Нажми на ресурс, чтобы проверить права, ссылку или срок."]
    return "\n".join(lines)


def _target_text(target) -> str:
    if target.setup_status == "verified":
        rights = "✅ администратор"
    else:
        rights = f"⛔ {escape(target.last_error or target.setup_status)}"
    return "\n".join(
        [
            f"🗂 {escape(target.title or str(target.chat_id))}",
            "",
            f"chat_id: {target.chat_id} · тип: {escape(target.chat_type)}",
            f"Ссылка: {escape(target.join_url) or '— не задана'}",
            f"Права бота: {rights}",
            f"Проверен: {core.fmt_when(target.last_checked_at)}",
            f"Окно: {_window_line(target.starts_at, target.ends_at)}",
            f"Состояние: {'активен' if target.is_active else 'выключен'}",
        ]
    )


def _diag_text(data) -> str:
    targets = data["targets"]
    lines = [
        "🩺 Диагностика",
        "",
        f"Требование: {'действует' if data['required'] else 'не действует'}",
        f"Ресурсов: {len(targets)} · активных {len(data['live_targets'])}",
        f"Ошибок API за 24 ч: {data['errors_24h']}",
        "",
        "Права бота:",
    ]
    if not targets:
        lines.append("— ресурсов нет")
    for target in targets:
        if target.setup_status == "verified":
            lines.append(
                f"✅ {escape(target.title or str(target.chat_id))} — администратор"
            )
        else:
            lines.append(
                f"⛔ {escape(target.title or str(target.chat_id))} — "
                f"{escape(target.last_error or target.setup_status)}"
            )
    if data["errors"]:
        lines += ["", "Последние ошибки проверки:"]
        for error in data["errors"][:5]:
            lines.append(
                f"• {core.fmt_when(error.created_at)} · ресурс {error.target_id or '—'} · "
                f"{escape(error.detail)} · user {error.user_id or '—'}"
            )
    return "\n".join(lines)


def _audit_text(rows) -> str:
    if not rows:
        return "🧾 Журнал действий\n\nПока пусто."
    lines = ["🧾 Журнал действий ОП", ""]
    for row in rows:
        payload = escape(json.dumps(row.payload or {}, ensure_ascii=False))
        lines.append(
            f"• {core.fmt_when(row.created_at)} · {escape(row.action)} · админ {row.actor_id or '—'}"
        )
        if payload and payload != "{}":
            lines.append(f"   {payload[:200]}")
    return "\n".join(lines)


async def _render(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return
        await safe_send(callback.message, text, reply_markup=markup)
    except Exception:
        await safe_send(callback.message, text, reply_markup=markup)


async def _show_root(callback: CallbackQuery) -> None:
    data = await panel.overview(callback.bot.id)
    settings = data["settings"]
    await _render(
        callback,
        _root_text(data),
        op_admin_kb(settings.enabled, settings.paused),
    )


@op_admin_router.callback_query(F.data == "admin:op")
async def cb_op_root(callback: CallbackQuery):
    await callback.answer()
    await _show_root(callback)


@op_admin_router.callback_query(F.data == "admin:op:on")
async def cb_op_on(callback: CallbackQuery):
    await panel.set_flag(callback.bot.id, callback.from_user.id, "enabled", True)
    await callback.answer("ОП включена")
    await _show_root(callback)


@op_admin_router.callback_query(F.data == "admin:op:off")
async def cb_op_off(callback: CallbackQuery):
    await panel.set_flag(callback.bot.id, callback.from_user.id, "enabled", False)
    await callback.answer("ОП выключена")
    await _show_root(callback)


@op_admin_router.callback_query(F.data == "admin:op:pause")
async def cb_op_pause(callback: CallbackQuery):
    data = await panel.overview(callback.bot.id)
    settings = data["settings"]
    await panel.set_flag(
        callback.bot.id, callback.from_user.id, "paused", not settings.paused
    )
    await callback.answer("Пауза снята" if settings.paused else "ОП на паузе")
    await _show_root(callback)


@op_admin_router.callback_query(F.data == "admin:op:res")
async def cb_op_resources(callback: CallbackQuery):
    targets = await panel.list_targets(callback.bot.id)
    await callback.answer()
    await _render(callback, _resources_text(targets), op_resources_kb(targets))


@op_admin_router.callback_query(F.data == "admin:op:radd")
async def cb_op_target_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OpAdminTarget.ADD)
    await callback.answer()
    await safe_send(callback.message, ADD_PROMPT, reply_markup=op_cancel_kb())


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:r:\d+$"))
async def cb_op_target(callback: CallbackQuery):
    target_id = int(callback.data.rsplit(":", 1)[1])
    target = await panel.get_target(callback.bot.id, target_id)
    if target is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    await callback.answer()
    await _render(callback, _target_text(target), op_target_kb(target))


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:rc:\d+$"))
async def cb_op_target_check(callback: CallbackQuery):
    target_id = int(callback.data.rsplit(":", 1)[1])
    target = await panel.check_target(callback.bot, callback.bot.id, target_id)
    if target is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    if target.setup_status == "verified":
        await callback.answer("Права в порядке: администратор")
    else:
        await callback.answer(f"Ошибка прав: {target.last_error}", show_alert=True)
    await _render(callback, _target_text(target), op_target_kb(target))


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:rt:\d+$"))
async def cb_op_target_toggle(callback: CallbackQuery):
    target_id = int(callback.data.rsplit(":", 1)[1])
    target = await panel.get_target(callback.bot.id, target_id)
    if target is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    updated = await panel.set_target_active(
        callback.bot.id, callback.from_user.id, target_id, not target.is_active
    )
    await callback.answer("Ресурс включён" if updated.is_active else "Ресурс выключен")
    await _render(callback, _target_text(updated), op_target_kb(updated))


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:rl:\d+$"))
async def cb_op_target_link(callback: CallbackQuery, state: FSMContext):
    target_id = int(callback.data.rsplit(":", 1)[1])
    target = await panel.get_target(callback.bot.id, target_id)
    if target is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    await state.set_state(OpAdminTarget.LINK)
    await state.update_data(op_target=target_id)
    await callback.answer()
    await safe_send(
        callback.message,
        LINK_PROMPT,
        reply_markup=op_cancel_kb(f"admin:op:r:{target_id}"),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:rd:\d+$"))
async def cb_op_target_delete_ask(callback: CallbackQuery):
    target_id = int(callback.data.rsplit(":", 1)[1])
    target = await panel.get_target(callback.bot.id, target_id)
    if target is None:
        await callback.answer("Ресурс не найден", show_alert=True)
        return
    await callback.answer()
    await _render(
        callback,
        f"Удалить ресурс «{escape(target.title or str(target.chat_id))}»?\n\n"
        "Он исчезнет из всех правил ОП. Действие необратимо.",
        op_confirm_delete_kb(target_id),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:rd2:\d+$"))
async def cb_op_target_delete(callback: CallbackQuery):
    target_id = int(callback.data.rsplit(":", 1)[1])
    removed = await panel.delete_target(
        callback.bot.id, callback.from_user.id, target_id
    )
    await callback.answer("Удалён" if removed else "Ресурс не найден")
    targets = await panel.list_targets(callback.bot.id)
    await _render(callback, _resources_text(targets), op_resources_kb(targets))


@op_admin_router.message(StateFilter(OpAdminTarget.ADD))
async def op_target_add_input(message: Message, state: FSMContext, bot):
    chat = message.forward_from_chat
    if chat is None and message.forward_origin is not None:
        origin = message.forward_origin
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    try:
        if chat is None:
            if not message.text:
                await message.answer(
                    "Нужен форвард из чата или @username / chat_id.",
                    reply_markup=op_cancel_kb(),
                )
                return
            chat = await panel.resolve_chat(bot, message.text)
        target, error = await panel.add_target(bot, bot.id, message.from_user.id, chat)
    except ValueError as problem:
        await message.answer(
            f"Не понял: {escape(str(problem))}\n\n{ADD_PROMPT}",
            reply_markup=op_cancel_kb(),
        )
        return
    except Exception as problem:
        logger.warning(
            "event=op_target_resolve_failed error=%s", type(problem).__name__
        )
        await message.answer(
            "Не смог получить чат. Проверь, что бот добавлен в него и видит историю.",
            reply_markup=op_cancel_kb(),
        )
        return
    await state.clear()
    note = ""
    if error:
        note = (
            "\n\n⚠️ Бот не администратор чата — Telegram не отдаст подписки. "
            "Выдай права и нажми «Проверить права»."
        )
    await message.answer(_target_text(target) + note, reply_markup=op_target_kb(target))


@op_admin_router.message(StateFilter(OpAdminTarget.LINK), F.text)
async def op_target_link_input(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("op_target")
    if target_id is None:
        await state.clear()
        await message.answer(
            "Не понял, к какому ресурсу ссылка.",
            reply_markup=op_single_kb("admin:op", "← К ОП"),
        )
        return
    try:
        target = await panel.set_join_url(
            message.bot.id, message.from_user.id, int(target_id), message.text.strip()
        )
    except ValueError as problem:
        await message.answer(
            f"Ссылка не подошла: {escape(str(problem))}",
            reply_markup=op_cancel_kb(f"admin:op:r:{target_id}"),
        )
        return
    if target is None:
        await state.clear()
        await message.answer(
            "Ресурс не найден.", reply_markup=op_single_kb("admin:op", "← К ОП")
        )
        return
    await state.clear()
    await message.answer(
        "Ссылка сохранена.\n\n" + _target_text(target),
        reply_markup=op_target_kb(target),
    )


@op_admin_router.message(
    Command("cancel"),
    StateFilter(
        OpAdminTarget.ADD,
        OpAdminTarget.LINK,
        OpAdminMessage.TEXT,
        OpAdminMessage.BUTTON,
    ),
)
async def op_admin_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=op_single_kb("admin:op", "← К ОП"))


@op_admin_router.callback_query(F.data == "admin:op:diag")
async def cb_op_diag(callback: CallbackQuery):
    data = await panel.diagnostics(callback.bot.id)
    await callback.answer()
    await _render(callback, _diag_text(data), op_diag_kb())


@op_admin_router.callback_query(F.data == "admin:op:diag:check")
async def cb_op_diag_check(callback: CallbackQuery):
    result = await panel.check_all(callback.bot, callback.bot.id)
    await callback.answer(f"Проверено. ОК: {result['ok']} · ошибок: {result['error']}")
    data = await panel.diagnostics(callback.bot.id)
    await _render(callback, _diag_text(data), op_diag_kb())


@op_admin_router.callback_query(F.data == "admin:op:audit")
async def cb_op_audit(callback: CallbackQuery):
    rows = await panel.audit_page(callback.bot.id)
    await callback.answer()
    await _render(callback, _audit_text(rows), op_audit_kb())


# ── Сообщения: шаблоны и подписи кнопок ─────────────────────────

SCOPE_TITLES = {"global": "Глобальный шаблон", "error": "Техническая ошибка"}
VARIABLES_HINT = (
    "Переменные: {first_name}, {username}, {bot_name}, {missing_count}, "
    "{function_name}."
)
TAGS_HINT = (
    "HTML: <b>, <i>, <u>, <s>, <a href>, <code>, <pre>, <blockquote>, "
    "<tg-spoiler>. MarkdownV2: *жирный*, _курсив_, [текст](url), `код`."
)


def _token(scope: str, key: str | None = None) -> str:
    if scope == "global":
        return "g"
    if scope == "error":
        return "e"
    return f"f:{key}"


def _parse_token(token: str) -> tuple[str, str | None]:
    if token == "g":
        return "global", None
    if token == "e":
        return "error", None
    if token.startswith("f:") and len(token) > 2:
        return "feature", token[2:]
    raise ValueError("Неизвестный шаблон.")


def _scope_title(scope: str, key: str | None = None) -> str:
    if scope == "feature":
        return f"Функция: {core.feature_name(key or '', 'ru')}"
    return SCOPE_TITLES[scope]


def _editor_text(scope: str, key: str | None, row) -> str:
    fmt = row.format if row else "html"
    lines = [
        f"✉️ {_scope_title(scope, key)}",
        f"Формат: {panel.FORMAT_TITLES.get(fmt, fmt)}",
        f"Состояние: {'свой шаблон' if row else 'встроенный текст'}",
    ]
    if row is not None and row.updated_at:
        lines.append(f"Обновлён: {core.fmt_when(row.updated_at)}")
    text = row.text if row else core.default_text(scope)
    lines += ["", "Текст:", f"<pre>{escape(text)}</pre>"]
    if scope != "error":
        lines += [
            "",
            VARIABLES_HINT,
            "Названия ресурсов со статусами добавляются автоматически.",
        ]
    return "\n".join(lines)


def _messages_text(rows, keys) -> str:
    own = {(row.scope, row.feature_key) for row in rows}
    lines = [
        "✉️ Сообщения ОП",
        "",
        f"🌐 Глобальный: {'свой' if ('global', None) in own else 'встроенный'}",
        f"⚠️ Ошибка проверки: {'свой' if ('error', None) in own else 'встроенный'}",
    ]
    if keys:
        lines += ["", "По функциям:"]
        for key in keys:
            state = "свой" if ("feature", key) in own else "встроенный"
            lines.append(f"• {core.feature_name(key, 'ru')}: {state}")
    lines += ["", "Шаблон функции сильнее глобального; экран ошибки — отдельный."]
    return "\n".join(lines)


def _messages_entries(rows, keys) -> list[tuple[str, str]]:
    own = {(row.scope, row.feature_key) for row in rows}
    entries = [
        ("g", f"🌐 Глобальный · {'свой' if ('global', None) in own else 'встроенный'}"),
        ("e", f"⚠️ Ошибка · {'свой' if ('error', None) in own else 'встроенный'}"),
    ]
    for key in keys:
        mark = "✏️" if ("feature", key) in own else "·"
        entries.append((f"f:{key}", f"{mark} {core.feature_name(key, 'ru')}"))
    return entries


def _buttons_text(settings) -> str:
    lines = ["🔘 Подписи кнопок", ""]
    for field, title in panel.BUTTON_TITLES.items():
        own = getattr(settings, panel.BUTTON_FIELDS[field])
        value = own or core.button_default(field)
        lines.append(f"{title}: «{escape(value)}» — {'своя' if own else 'по умолчанию'}")
    lines += ["", "Кнопку проверки убрать нельзя — можно только переименовать."]
    return "\n".join(lines)


@op_admin_router.callback_query(F.data == "admin:op:msg")
async def cb_op_messages(callback: CallbackQuery):
    rows = await panel.list_templates(callback.bot.id)
    keys = await panel.feature_keys(callback.bot.id)
    await callback.answer()
    await _render(callback, _messages_text(rows, keys), op_messages_kb(_messages_entries(rows, keys)))


@op_admin_router.callback_query(F.data == "admin:op:msg:add")
async def cb_op_messages_add(callback: CallbackQuery):
    rows = await panel.list_templates(callback.bot.id)
    keys = await panel.feature_keys(callback.bot.id)
    entries = _messages_entries(rows, keys)[2:]
    await callback.answer()
    await _render(
        callback,
        "➕ Шаблон для функции\n\nВыбери функцию — её текст заменит глобальный только для неё.",
        op_messages_kb(entries),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:msg:.+$"))
async def cb_op_message_editor(callback: CallbackQuery):
    token = callback.data.split(":", 3)[3]
    try:
        scope, key = _parse_token(token)
    except ValueError:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    row = await panel.get_template(callback.bot.id, scope, key)
    await callback.answer()
    fmt = panel.FORMAT_TITLES.get(row.format, row.format) if row else "HTML"
    await _render(
        callback,
        _editor_text(scope, key, row),
        op_message_editor_kb(token, row is not None, fmt),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:msge:.+$"))
async def cb_op_message_edit(callback: CallbackQuery, state: FSMContext):
    token = callback.data.split(":", 3)[3]
    try:
        scope, key = _parse_token(token)
    except ValueError:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    row = await panel.get_template(callback.bot.id, scope, key)
    fmt = row.format if row else "html"
    current = row.text if row else core.default_text(scope)
    await state.set_state(OpAdminMessage.TEXT)
    await state.update_data(op_scope=token, op_format=fmt)
    await callback.answer()
    await safe_send(
        callback.message,
        "✏️ Новый текст шаблона\n\n"
        f"Формат: {panel.FORMAT_TITLES.get(fmt, fmt)}. {TAGS_HINT}\n"
        f"{VARIABLES_HINT}\n\n"
        "Сейчас:\n"
        f"<pre>{escape(current)}</pre>\n\n"
        "Отправь новый текст одним сообщением. Отмена — /cancel.",
        reply_markup=op_cancel_kb(f"admin:op:msg:{token}"),
    )


@op_admin_router.message(StateFilter(OpAdminMessage.TEXT), F.text)
async def op_message_text_input(message: Message, state: FSMContext):
    data = await state.get_data()
    token = data.get("op_scope")
    fmt = data.get("op_format") or "html"
    try:
        scope, key = _parse_token(token or "")
        await panel.save_template(
            message.bot.id,
            message.from_user.id,
            scope,
            key,
            message.text,
            fmt,
        )
    except ValueError as problem:
        await message.answer(
            f"Шаблон не сохранён: {escape(str(problem))}\n\n"
            "Отправь другой текст или /cancel.",
            reply_markup=op_cancel_kb(f"admin:op:msg:{token}"),
        )
        return
    await state.clear()
    await message.answer(
        "✅ Шаблон сохранён.",
        reply_markup=op_single_kb(f"admin:op:msg:{token}", "← К шаблону"),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:msgf:.+$"))
async def cb_op_message_format(callback: CallbackQuery):
    token = callback.data.split(":", 3)[3]
    try:
        scope, key = _parse_token(token)
    except ValueError:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    row = await panel.get_template(callback.bot.id, scope, key)
    if row is None:
        await callback.answer("Сначала задай текст", show_alert=True)
        return
    order = list(panel.FORMATS)
    current = row.format if row.format in order else "html"
    fmt = order[(order.index(current) + 1) % len(order)]
    updated = await panel.set_template_format(
        callback.bot.id, callback.from_user.id, scope, key, fmt
    )
    await callback.answer(f"Формат: {panel.FORMAT_TITLES[fmt]}")
    await _render(
        callback,
        _editor_text(scope, key, updated),
        op_message_editor_kb(token, True, panel.FORMAT_TITLES.get(fmt, fmt)),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:msgp:.+$"))
async def cb_op_message_preview(callback: CallbackQuery):
    token = callback.data.split(":", 3)[3]
    try:
        scope, key = _parse_token(token)
    except ValueError:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    row = await panel.get_template(callback.bot.id, scope, key)
    fmt = row.format if row else "html"
    text = row.text if row else core.default_text(scope)
    context = {
        "first_name": "Аня",
        "username": "@anya",
        "bot_name": bots.label(callback.bot.id),
        "missing_count": 2,
        "function_name": core.feature_name(key or "*", "ru"),
    }
    rendered = core.render(text, context, fmt)
    if scope != "error":
        rendered += "\n\n" + core.tr(core.MISSING, "ru").format(
            titles="Канал А, Группа Б"
        )
    parse_mode = {"html": "HTML", "markdown": "MarkdownV2"}.get(fmt)
    await callback.answer()
    try:
        await safe_send(
            callback.message,
            "👁 Предпросмотр\n\n" + rendered,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as error:
        await safe_send(
            callback.message,
            f"⚠️ Telegram не принял разметку: {escape(str(error))}",
            reply_markup=op_single_kb(f"admin:op:msg:{token}", "← К шаблону"),
        )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:msgd:.+$"))
async def cb_op_message_reset_ask(callback: CallbackQuery):
    token = callback.data.split(":", 3)[3]
    await callback.answer()
    await _render(
        callback,
        "♻️ Сбросить шаблон к встроенному тексту?\n\n"
        "Своя формулировка удалится, подписи кнопок останутся.",
        op_confirm_reset_kb(token),
    )


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:msgd2:.+$"))
async def cb_op_message_reset(callback: CallbackQuery):
    token = callback.data.split(":", 3)[3]
    try:
        scope, key = _parse_token(token)
    except ValueError:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    removed = await panel.delete_template(
        callback.bot.id, callback.from_user.id, scope, key
    )
    await callback.answer("Сброшено" if removed else "Уже встроенный")
    await _render(
        callback,
        _editor_text(scope, key, None),
        op_message_editor_kb(token, False),
    )


@op_admin_router.callback_query(F.data == "admin:op:btn")
async def cb_op_buttons(callback: CallbackQuery):
    settings = await panel.get_settings(callback.bot.id)
    await callback.answer()
    await _render(callback, _buttons_text(settings), op_buttons_kb())


@op_admin_router.callback_query(F.data.regexp(r"^admin:op:btn:(join|check|back)$"))
async def cb_op_button_edit(callback: CallbackQuery, state: FSMContext):
    field = callback.data.rsplit(":", 1)[1]
    settings = await panel.get_settings(callback.bot.id)
    own = getattr(settings, panel.BUTTON_FIELDS[field])
    current = own or core.button_default(field)
    await state.set_state(OpAdminMessage.BUTTON)
    await state.update_data(op_button=field)
    await callback.answer()
    await safe_send(
        callback.message,
        f"✏️ Подпись кнопки «{panel.BUTTON_TITLES[field]}»\n\n"
        f"Сейчас: «{escape(current)}»\n\n"
        "Отправь новую подпись (до 64 символов). Слово «сброс» вернёт "
        "значение по умолчанию.\nОтмена — /cancel.",
        reply_markup=op_cancel_kb("admin:op:btn"),
    )


@op_admin_router.message(StateFilter(OpAdminMessage.BUTTON), F.text)
async def op_button_input(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("op_button")
    if field not in panel.BUTTON_FIELDS:
        await state.clear()
        await message.answer(
            "Не понял, какую кнопку менять.",
            reply_markup=op_single_kb("admin:op:btn", "← К подписям"),
        )
        return
    text = message.text.strip()
    value = None if text.lower() in {"сброс", "reset"} else text
    try:
        settings = await panel.set_button_label(
            message.bot.id, message.from_user.id, field, value
        )
    except ValueError as problem:
        await message.answer(
            f"Не сохранено: {escape(str(problem))}\n\nОтправь другую подпись или /cancel.",
            reply_markup=op_cancel_kb("admin:op:btn"),
        )
        return
    await state.clear()
    await message.answer("✅ Подпись сохранена.\n\n" + _buttons_text(settings), reply_markup=op_buttons_kb())
