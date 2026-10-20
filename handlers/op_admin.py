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
    op_cancel_kb,
    op_confirm_delete_kb,
    op_diag_kb,
    op_resources_kb,
    op_single_kb,
    op_target_kb,
)
from services import op as core
from services import op_admin as panel
from utils.time import utcnow

logger = logging.getLogger(__name__)


class OpAdminTarget(StatesGroup):
    ADD = State()
    LINK = State()


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
    Command("cancel"), StateFilter(OpAdminTarget.ADD, OpAdminTarget.LINK)
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
