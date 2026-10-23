import logging
import re
from html import escape
import config
from aiogram import F, BaseMiddleware
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from handlers import admin_router
from keyboards import texts as T
from keyboards.inline import (
    admin_kb,
    admin_report_kb,
    admin_broadcast_audience_kb,
    admin_user_card_kb,
    admin_broadcast_status_kb,
    admin_ads_kb,
    IDEA_FILTERS,
    admin_ideas_kb,
    admin_ad_card_kb,
    admin_ads_settings_kb,
    admin_ads_settings_cancel_kb,
    SEGMENT_TITLES,
    back_kb,
    admin_confirm_ban_kb,
    admin_confirm_grant_kb,
    admin_confirm_mute_kb,
    admin_back_kb,
    admin_analytics_kb,
    admin_users_section_kb,
    admin_moderation_kb,
    admin_broadcast_section_kb,
    admin_settings_section_kb,
    admin_system_kb,
    admin_growth_kb,
    admin_settings_kb,
    admin_setting_kb,
    admin_prices_kb,
    admin_audit_kb,
    admin_user_list_kb,
)
from services.admin import (
    AdminService,
    StatsService,
    authorize,
    BROADCAST_SEGMENTS,
    SETTING_LIMITS,
    SETTING_TITLES,
    SETTING_DEFAULTS,
    SETTING_PRESETS,
    SETTING_KEYS,
    SETTING_PAGE_SIZE,
)
from services.ads import AdCampaignService, CHANNEL_USERNAME_RE
from services.payments import PaymentService
from services.chat_manager import get_online_count, get_partner


logger = logging.getLogger(__name__)
CUSTOM_EMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')


async def safe_send(message, text, **kwargs):
    """Send text, falling back to the plain emoji if Telegram refuses an id.

    One unusable custom emoji id makes Telegram reject the entire message with
    DOCUMENT_INVALID, which left the admin panel unreachable. The fallback keeps
    the emoji character and drops only the tag around it, so the panel still
    opens while the bad id is fixed.
    """
    try:
        return await message.answer(text, **kwargs)
    except TelegramBadRequest as error:
        if "DOCUMENT_INVALID" not in str(error):
            raise
        logger.warning("event=custom_emoji_rejected chat_id=%s", message.chat.id)
        return await message.answer(CUSTOM_EMOJI.sub(r"\1", text), **kwargs)


class AdminOnly(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            authorize(event.from_user.id)
        except PermissionError:
            await event.answer("⛔ Нет доступа.")
            return
        return await handler(event, data)


admin_router.message.middleware(AdminOnly())
admin_router.callback_query.middleware(AdminOnly())


class AdminBroadcast(StatesGroup):
    CONTENT = State()
    POLL_QUESTION = State()
    POLL_OPTIONS = State()


class AdminMessage(StatesGroup):
    CONTENT = State()


class AdminAdCampaign(StatesGroup):
    CHANNEL = State()
    TITLE = State()
    SUBTITLE = State()
    BODY = State()
    LIMITS = State()


class AdminAdSettings(StatesGroup):
    COOLDOWN = State()


class AdminSeason(StatesGroup):
    CODE = State()
    START = State()
    END = State()
    DISCOUNT = State()


class AdminIdeaReply(StatesGroup):
    TEXT = State()


class AdminIdeaDuplicate(StatesGroup):
    ORIGINAL = State()


class AdminSettingValue(StatesGroup):
    VALUE = State()


class AdminPriceValue(StatesGroup):
    VALUE = State()


@admin_router.message(Command("season"))
async def cmd_season(message: Message):
    parts = message.text.split()
    if len(parts) != 5 or not parts[4].isdigit():
        await message.answer(
            "/season код начало_ISO конец_ISO скидка\nДаты с часовым поясом, например 2026-10-01T00:00:00+00:00. Включение: /setting seasonal_enabled 1"
        )
        return
    result = await AdminService().seasonal(
        message.from_user.id, parts[1], parts[2], parts[3], int(parts[4])
    )
    await message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Событие сохранено. Оно действует только в заданный период и при seasonal_enabled=1.'
        if result.ok
        else "Проверь код, даты с часовым поясом и скидку 1–90%."
    )


def season_discount_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{n}%", callback_data=f"admin:season:discount:{n}"
                )
                for n in (10, 20, 30, 50)
            ]
        ]
    )


@admin_router.message(
    Command("cancel"),
    StateFilter(
        AdminSeason.CODE, AdminSeason.START, AdminSeason.END, AdminSeason.DISCOUNT
    ),
)
async def season_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Создание сезонного события отменено.", reply_markup=admin_kb()
    )


@admin_router.callback_query(F.data == "admin:season")
async def cb_season(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSeason.CODE)
    await callback.answer()
    await callback.message.answer(
        "Код события: латиница, цифры, подчёркивание, до 32 символов (например black_friday):\n/cancel — отменить."
    )


@admin_router.message(
    AdminSeason.CODE, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def season_code(message: Message, state: FSMContext):
    if not re.fullmatch(r"[a-z0-9_]{1,32}", message.text.strip()):
        await message.answer(
            "Только латиница, цифры и «_», до 32 символов. Попробуй ещё раз."
        )
        return
    await state.update_data(code=message.text.strip())
    await state.set_state(AdminSeason.START)
    await message.answer(
        "Дата и время начала, с часовым поясом (например 2026-10-01T00:00:00+00:00):"
    )


@admin_router.message(
    AdminSeason.START, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def season_start(message: Message, state: FSMContext):
    from datetime import datetime

    try:
        start = datetime.fromisoformat(message.text.strip())
        if start.tzinfo is None:
            raise ValueError
    except ValueError:
        await message.answer(
            "Нужна дата с часовым поясом, например 2026-10-01T00:00:00+00:00. Попробуй ещё раз."
        )
        return
    await state.update_data(starts_at=message.text.strip())
    await state.set_state(AdminSeason.END)
    await message.answer("Дата и время окончания, с часовым поясом:")


@admin_router.message(
    AdminSeason.END, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def season_end(message: Message, state: FSMContext):
    from datetime import datetime
    from utils.time import utcnow

    data = await state.get_data()
    try:
        start = datetime.fromisoformat(data["starts_at"])
        end = datetime.fromisoformat(message.text.strip())
        if end.tzinfo is None or end <= start or end <= utcnow():
            raise ValueError
    except ValueError:
        await message.answer(
            "Нужна дата с часовым поясом, позже даты начала и позже текущего момента. Попробуй ещё раз."
        )
        return
    await state.update_data(ends_at=message.text.strip())
    await state.set_state(AdminSeason.DISCOUNT)
    await message.answer(
        "Скидка в процентах (1–90) — выбери кнопкой или пришли число:",
        reply_markup=season_discount_kb(),
    )


async def _season_finish(admin_id, code, starts_at, ends_at, discount, state):
    result = await AdminService().seasonal(admin_id, code, starts_at, ends_at, discount)
    await state.clear()
    return result


@admin_router.message(
    AdminSeason.DISCOUNT, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def season_discount_text(message: Message, state: FSMContext):
    if not message.text.strip().isdigit() or not 1 <= int(message.text.strip()) <= 90:
        await message.answer(
            "Нужно число от 1 до 90.", reply_markup=season_discount_kb()
        )
        return
    data = await state.get_data()
    result = await _season_finish(
        message.from_user.id,
        data["code"],
        data["starts_at"],
        data["ends_at"],
        int(message.text.strip()),
        state,
    )
    await message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Событие сохранено. Оно действует только в заданный период и при seasonal_enabled=1.'
        if result.ok
        else "Не удалось сохранить — проверь код и даты.",
        reply_markup=admin_kb(),
    )


@admin_router.callback_query(
    AdminSeason.DISCOUNT, F.data.startswith("admin:season:discount:")
)
async def season_discount_button(callback: CallbackQuery, state: FSMContext):
    discount = int(callback.data.split(":")[-1])
    data = await state.get_data()
    result = await _season_finish(
        callback.from_user.id,
        data["code"],
        data["starts_at"],
        data["ends_at"],
        discount,
        state,
    )
    await callback.answer()
    await callback.message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Событие сохранено. Оно действует только в заданный период и при seasonal_enabled=1.'
        if result.ok
        else "Не удалось сохранить — проверь код и даты.",
        reply_markup=admin_kb(),
    )


def overview_text(s):
    metric = lambda key: (
        f"{s[key]}%" if s.get(key) is not None else "— (недостаточно данных)"
    )
    growth = (
        metric("growth_7d_pct")
        if s.get("growth_7d_pct") is not None
        else "— (недостаточно данных за прошлую неделю)"
    )
    stars_usd = f" (≈${s['stars_usd']})" if s.get("stars_usd") is not None else ""
    return f'<tg-emoji emoji-id="6129805886383723340">🛠</tg-emoji> Админ-панель\n\n<tg-emoji emoji-id="5215522595922779944">👥</tg-emoji> Пользователей: {s["users"]}\n<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Новых за период: {s["new"]}\nНовых за 7 дней: {s["new_7d"]} ({growth} к предыдущим 7 дням)\n🚀 Всего запусков: {s["starts"]}\n🚀 Запусков сегодня (Киев): {s["starts_today"]}\n\n<tg-emoji emoji-id="5399909394525737759">📈</tg-emoji> Воронка\nРегистрацию завершили: {s["onboarding_completed"]} ({metric("onboarding_conversion")})\nНачали поиск: {s["searchers"]}\nПолучили пару: {s["matched"]} ({metric("search_conversion")})\n<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Завершённых диалогов: {s["chats"]}\n<tg-emoji emoji-id="5258258882022612173">⏱</tg-emoji> Средняя длительность: {s["avg_seconds"]} сек.\n<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Оплатили: {s["buyers"]} ({metric("purchase_conversion")})\nПолучено: {s["stars"]}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji>{stars_usd}\n<tg-emoji emoji-id="5283130719806175947">💎</tg-emoji> Активных Premium: {s["premium"]}\n<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> Средний рейтинг: {metric("rating")}\nВозврат на следующий день: {metric("retention_d1")}\nВозврат через 7 дней: {metric("retention_d7")}'


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    s = await StatsService().overview()
    await safe_send(
        message,
        overview_text(s)
        + f'\n<tg-emoji emoji-id="5215522595922779944">🟢</tg-emoji> Онлайн: {await get_online_count()}',
        reply_markup=admin_kb(),
    )


@admin_router.callback_query(F.data == "admin:back")
async def cb_admin_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # "Назад" is reachable from mid-flow screens (message-to-user, broadcast
    # compose) that have no dedicated cancel -- without this, a stale FSM
    # state survives and the admin's next unrelated text gets consumed by it.
    await state.clear()
    s = await StatsService().overview()
    await safe_send(
        callback.message,
        overview_text(s)
        + f'\n<tg-emoji emoji-id="5215522595922779944">🟢</tg-emoji> Онлайн: {await get_online_count()}',
        reply_markup=admin_kb(),
    )


def funnel_text(f, days, min_step_pct=0):
    total = f["stages"][0][1]
    head = f'<tg-emoji emoji-id="5399909394525737759">📉</tg-emoji> Воронка регистрации за {days} дн.'
    if not total:
        return head + "\n\nЗа период никто не запускал бота."
    lines, warnings, previous, previous_label = [], [], None, None
    for label, count in f["stages"]:
        step_pct = round(100 * count / previous) if previous else 0
        share = f"{round(100 * count / total)}% от всех"
        if previous is not None:
            share += f", {step_pct}% от предыдущего"
            if min_step_pct and previous and step_pct < min_step_pct:
                warnings.append(
                    f"⚠️ Просадка на шаге «{label}»: {step_pct}% от «{previous_label}» (порог {min_step_pct}%)"
                )
        lines.append(f"{label}: {count} ({share})")
        previous, previous_label = count, label
    text = head + "\n\n" + "\n".join(lines)
    if warnings:
        text += "\n\n" + "\n".join(warnings)
    text += f"\n\nНе нажали ни одной кнопки после /start: {f['stages'][0][1] - f['stages'][1][1]}"
    if f["age_rejected"]:
        text += f"\nОтклонено на шаге возраста: {f['age_rejected']}"
    if f["avg_minutes"] is not None:
        text += f"\nСреднее время регистрации: {f['avg_minutes']} мин."
    if len(f["daily"]) > 1:
        text += "\n\nПо дням (новых / дошли до конца):\n" + "\n".join(
            f"{day}: {new} / {done} ({round(100 * done / new) if new else 0}%)"
            for day, new, done in f["daily"]
        )
    return text


def funnel_kb(days):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="admin:funnel:1"),
                InlineKeyboardButton(text="7 дней", callback_data="admin:funnel:7"),
                InlineKeyboardButton(text="30 дней", callback_data="admin:funnel:30"),
            ],
            [
                InlineKeyboardButton(
                    text="События", callback_data=f"admin:events:{days}"
                ),
                InlineKeyboardButton(text="Назад", callback_data="admin:sec:analytics"),
            ],
        ]
    )


@admin_router.callback_query(F.data == "admin:funnel")
@admin_router.callback_query(F.data.startswith("admin:funnel:"))
async def cb_funnel(callback: CallbackQuery):
    value = callback.data.split(":")[-1]
    days = int(value) if value.isdigit() else 1
    if days not in {1, 7, 30}:
        await callback.answer("Недопустимый период.")
        return
    from services.settings import bot_value

    min_step_pct = await bot_value("funnel_min_step_pct", 0)
    await callback.answer()
    await safe_send(
        callback.message,
        funnel_text(await StatsService().onboarding_funnel(days), days, min_step_pct),
        reply_markup=funnel_kb(days),
    )


def growth_text(g, days):
    window = "24 ч" if days == 1 else f"{days} дн."
    lines = [f"📈 Рост и возвраты за {window}"]
    if g["jobs"]:
        lines.append("📨 Рассылки:")
        for j in g["jobs"]:
            when = j["created_at"].strftime("%d.%m %H:%M")
            pct = round(100 * j["returned"] / j["sent"]) if j["sent"] else 0
            lines.append(
                f"#{j['id']} от {when} — {j['sent']} доставлено, {j['failed']} ошибок, {j['queued']} в очереди"
            )
            lines.append(
                f"   ↳ вернулись {j['returned']} ({pct}%), из них в диалоге {j['chatted']}"
            )
    else:
        lines.append("📨 Рассылок за период не было.")
    labels = {
        "inactive": "спящим",
        "prime_time": "вечерний пик",
        "onboarding_nudge": "брошенная регистрация",
    }
    if g["nudges"]:
        lines.append(
            "🔔 Напоминания: "
            + ", ".join(
                f"{labels.get(k, k)} {v}" for k, v in sorted(g["nudges"].items())
            )
        )
    if g["sources"]:
        lines.append(
            "🏷 Метки: " + ", ".join(f"{k} {v}" for k, v in sorted(g["sources"].items()))
        )
    lines.append(
        f"🆕 Новых {g['new']} (дошли до конца {g['completed']}) · Диалогов начато {g['chats']}"
    )
    return "\n".join(lines)


@admin_router.message(Command("growth"))
async def cmd_growth(message: Message):
    parts = message.text.split()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    if days not in {1, 7, 30}:
        await message.answer(
            "/growth [1|7|30] — рассылки, возвраты, напоминания и метки за период"
        )
        return
    g = await StatsService().growth(days)
    await safe_send(
        message, growth_text(g, days) + f"\n🟢 Онлайн: {await get_online_count()}"
    )


@admin_router.callback_query(F.data.in_({"admin:stats", "admin:money"}))
@admin_router.callback_query(F.data.startswith("admin:events:"))
@admin_router.callback_query(F.data.startswith("admin:period:"))
async def cb_stats(callback: CallbackQuery):
    days = (
        int(callback.data.split(":")[-1])
        if callback.data.split(":")[-1].isdigit()
        else 1
    )
    if days not in {1, 7, 30}:
        await callback.answer("Недопустимый период.")
        return
    s = await StatsService().overview(days)
    text = overview_text(s)
    if callback.data == "admin:money":
        text += "\n\nПо продуктам:\n" + "\n".join(
            f'{k}: {v}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> · '
            f"{s['product_orders'].get(k, 0)} покупок · {s['product_shares'].get(k, 0)}%"
            for k, v in s["products"].items()
        )
    if callback.data.startswith("admin:events:"):
        text += "\n\nУникальные пользователи по событиям:\n" + "\n".join(
            f"{k}: {v}" for k, v in s["funnel"].items()
        )
    await callback.answer()
    await safe_send(
        callback.message,
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="За неделю", callback_data="admin:period:7"
                    ),
                    InlineKeyboardButton(
                        text="За месяц", callback_data="admin:period:30"
                    ),
                    InlineKeyboardButton(
                        text="CSV", callback_data=f"admin:export:{days}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Назад", callback_data="admin:sec:analytics"
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(F.data.startswith("admin:export:"))
async def cb_export(callback: CallbackQuery):
    import csv, io

    value = callback.data.split(":")[-1]
    if value not in {"1", "7", "30"}:
        await callback.answer("Недопустимый период")
        return
    s = await StatsService().overview(int(value))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["metric", "value"])
    writer.writerows((k, v) for k, v in s.items() if not isinstance(v, dict))
    await callback.answer()
    await callback.message.answer_document(
        BufferedInputFile(
            buffer.getvalue().encode("utf-8-sig"), filename="statistics.csv"
        )
    )


@admin_router.callback_query(F.data == "admin:reports")
@admin_router.callback_query(F.data.startswith("admin:reports:"))
async def cb_reports(callback: CallbackQuery):
    value = callback.data.split(":")[-1]
    offset = int(value) if value.isdigit() else 0
    reports = await AdminService().reports(callback.from_user.id, offset)
    await callback.answer()
    if not reports:
        await callback.message.answer(
            '<tg-emoji emoji-id="5280880410346152584">🚩</tg-emoji> Жалоб нет.',
            reply_markup=admin_back_kb("admin:sec:moderation"),
        )
        return
    r = reports[0]
    names, previous, bans, mutes = await AdminService().report_details(
        callback.from_user.id, r
    )
    warnings = await AdminService().warnings_against(
        callback.from_user.id, r.reported_id
    )
    media = await AdminService().snapshot_count(callback.from_user.id, r.id)
    await callback.message.answer(
        f'<tg-emoji emoji-id="5280880410346152584">🚩</tg-emoji> Жалоба #{r.id}\nОт: {escape(names.get(r.reporter_id, "Удалённый профиль"))}\nНа: {escape(names.get(r.reported_id, "Удалённый профиль"))}\nПричина: {escape(r.reason)}\nДетали: {escape(r.message_content or "Не указаны")}\nИстория: {previous} других жалоб, {bans} банов, {mutes} мутов, {warnings} активных предупреждений\nДата: {r.created_at:%d.%m.%Y %H:%M} UTC',
        reply_markup=admin_report_kb(r.id, offset + 1, has_media=bool(media)),
    )


@admin_router.callback_query(F.data.startswith("admin:report:media:"))
async def cb_report_media(callback: CallbackQuery):
    value = callback.data.split(":")[-1]
    if not value.isdigit():
        await callback.answer("Недопустимая жалоба")
        return
    items = await AdminService().snapshots(callback.from_user.id, int(value))
    await callback.answer()
    if not items:
        await callback.message.answer("Снимков нет.")
        return
    for item in items:
        caption = f"Снимок жалобы #{item.report_id} · {item.kind}"
        try:
            if item.kind == "photo":
                await callback.message.answer_photo(item.file_id, caption=caption)
            elif item.kind == "voice":
                await callback.message.answer_voice(item.file_id, caption=caption)
            elif item.kind == "audio":
                await callback.message.answer_audio(item.file_id, caption=caption)
            elif item.kind == "video":
                await callback.message.answer_video(item.file_id, caption=caption)
            elif item.kind == "video_note":
                await callback.message.answer_video_note(item.file_id)
            elif item.kind == "sticker":
                await callback.message.answer_sticker(item.file_id)
            elif item.kind == "animation":
                await callback.message.answer_animation(item.file_id, caption=caption)
            elif item.text:
                await callback.message.answer(item.text)
        except Exception:
            # One expired file_id must not hide the rest of the snapshot.
            continue


@admin_router.callback_query(F.data.startswith("admin:ban1:"))
@admin_router.callback_query(F.data.startswith("admin:banf:"))
@admin_router.callback_query(F.data.startswith("admin:mute1:"))
@admin_router.callback_query(F.data.startswith("admin:mute24:"))
@admin_router.callback_query(F.data.startswith("admin:mute7:"))
@admin_router.callback_query(F.data.startswith("admin:warn:"))
@admin_router.callback_query(F.data.startswith("admin:dismiss:"))
async def cb_resolve(callback: CallbackQuery):
    parts = callback.data.split(":")
    if not parts[-1].isdigit():
        await callback.answer("Недопустимая жалоба")
        return
    result = await AdminService().resolve(
        callback.from_user.id, int(parts[-1]), parts[1]
    )
    await callback.answer("Решение сохранено" if result.ok else "Жалоба уже обработана")
    # Leaving just "Следующая" (not clearing the keyboard outright) keeps the
    # admin moving through the queue without a trip back through the menu.
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Следующая жалоба",
                        icon_custom_emoji_id="5967432491684860012",
                        callback_data="admin:reports:0",
                    )
                ]
            ]
        )
    )


@admin_router.callback_query(F.data == "admin:bans")
async def cb_bans(callback: CallbackQuery):
    users = await AdminService().bans(callback.from_user.id)
    await callback.answer()
    text = "\n".join(
        f"{u.anon_id} — "
        + (f"до {u.ban_expires:%d.%m.%Y}" if u.ban_expires else "навсегда")
        for u in users
    )
    await callback.message.answer(
        '<tg-emoji emoji-id="5312229088876847138">🚫</tg-emoji> Активные баны (до 50):\n'
        + text
        + "\n\n/ban_user ID часы причина\n/unban ID",
        reply_markup=back_kb("admin:sec:moderation"),
    )


@admin_router.callback_query(F.data == "admin:users")
async def cb_users(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        '<tg-emoji emoji-id="5215522595922779944">👥</tg-emoji> Введи anon_id или telegram_id пользователя:',
        reply_markup=back_kb("admin:sec:users"),
    )


async def user_card_text(admin_id, user):
    reports_count = await AdminService().reports_against(admin_id, user.telegram_id)
    warnings_count = await AdminService().warnings_against(admin_id, user.telegram_id)
    starts = user.start_count or 0
    events = await AdminService().start_events_count(user.telegram_id)
    started = f" (событий: {events})" if events != starts else ""
    premium = f"до {user.premium_expires:%d.%m.%Y}" if user.premium_expires else "нет"
    from services.onboarding import muted

    mute = "нет"
    if muted(user):
        mute = (
            f"до {user.mute_expires:%d.%m.%Y %H:%M} UTC"
            if user.mute_expires
            else "бессрочно"
        )
    return (
        f'<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> {escape(user.anon_id or "?")}\n'
        f"Telegram ID: {user.telegram_id}\n"
        f'<tg-emoji emoji-id="5258105663359294787">📅</tg-emoji> Регистрация: {user.created_at:%d.%m.%Y}\n'
        f'<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Диалогов: {user.chats_count}\n'
        f"🚀 Запусков: {starts}{started}\n"
        f'<tg-emoji emoji-id="5280880410346152584">🚩</tg-emoji> Жалоб на него: {reports_count}\n'
        f"⚠️ Активных предупреждений: {warnings_count}\n"
        f'<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium: {premium}\n'
        f"🔇 Мут: {mute}\n"
        f'<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Всего потратил: {user.total_stars_spent}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji>'
    )


@admin_router.callback_query(F.data.startswith("admin:user_card:"))
async def cb_user_card(callback: CallbackQuery):
    value = callback.data.split(":")[-1]
    user = await AdminService().find_user(callback.from_user.id, value)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        await user_card_text(callback.from_user.id, user),
        reply_markup=admin_user_card_kb(user.telegram_id),
    )


@admin_router.callback_query(F.data.startswith("admin:message:"))
async def cb_message_user(callback: CallbackQuery, state: FSMContext):
    target_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminMessage.CONTENT)
    await state.update_data(target_id=target_id)
    await callback.answer()
    await callback.message.answer(
        f'<tg-emoji emoji-id="5224455462178549802">💬</tg-emoji> Введи текст сообщения для пользователя {target_id}:'
    )


@admin_router.message(
    AdminMessage.CONTENT, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def send_message_to_user(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_id")
    await state.clear()
    if not target_id:
        await message.answer("Ошибка: не указан получатель.")
        return
    import uuid

    request_key = f"msg:{message.chat.id}:{message.message_id}:{uuid.uuid4().hex[:8]}"
    result = await AdminService().send_message_to_user(
        message.from_user.id, target_id, message.text, request_key=request_key
    )
    if result.ok:
        await message.answer(
            f'<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Сообщение отправлено пользователю {target_id}.'
        )
    else:
        await message.answer(f"❌ Ошибка: {result.code}")


@admin_router.callback_query(F.data.startswith("admin:uban:"))
async def cb_admin_ban_user(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[-1])
    await callback.answer()
    await callback.message.answer(
        f"⛔ Забанить {target_id} на сколько?",
        reply_markup=admin_confirm_ban_kb(target_id),
    )


@admin_router.callback_query(F.data.startswith("admin:umute:"))
async def cb_admin_mute_user(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[-1])
    await callback.answer()
    await callback.message.answer(
        f"🔇 Замутить {target_id} на сколько?",
        reply_markup=admin_confirm_mute_kb(target_id),
    )


@admin_router.callback_query(F.data.startswith("admin:domute:"))
async def cb_admin_domute(callback: CallbackQuery):
    parts = callback.data.split(":")
    target_id, hours = int(parts[2]), int(parts[3])
    result = await AdminService().mute(
        callback.from_user.id, target_id, "Решение администратора", hours or None
    )
    await callback.answer("Мут выдан" if result.ok else "Не удалось замутить")
    if result.ok:
        await callback.message.edit_text(
            f"🔇 {target_id}"
            + (" в муте навсегда." if not hours else f" в муте на {hours} ч.")
        )


@admin_router.callback_query(F.data.startswith("admin:unmute:"))
async def cb_admin_unmute_user(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[-1])
    result = await AdminService().unmute(callback.from_user.id, target_id)
    await callback.answer("Мут снят" if result.ok else "Не удалось снять мут")


@admin_router.callback_query(F.data.startswith("admin:ugrant:"))
async def cb_admin_grant_user(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[-1])
    await callback.answer()
    await callback.message.answer(
        f'<tg-emoji emoji-id="5312160339335347417">🎁</tg-emoji> Выдать Premium {target_id} на сколько?',
        reply_markup=admin_confirm_grant_kb(target_id),
    )


@admin_router.callback_query(F.data.startswith("admin:ucard:"))
async def cb_admin_ucard(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[-1])
    await callback.answer()
    user = await AdminService().find_user(callback.from_user.id, str(target_id))
    if not user:
        await callback.message.answer("Пользователь не найден.", reply_markup=back_kb())
        return
    await callback.message.answer(
        await user_card_text(callback.from_user.id, user),
        reply_markup=admin_user_card_kb(user.telegram_id),
    )


@admin_router.callback_query(F.data.startswith("admin:doban:"))
async def cb_admin_doban(callback: CallbackQuery):
    parts = callback.data.split(":")
    target_id, hours = int(parts[2]), int(parts[3])
    result = await AdminService().ban(
        callback.from_user.id, target_id, "Решение администратора", hours or None
    )
    await callback.answer("Забанен" if result.ok else "Не удалось забанить")
    if result.ok:
        await callback.message.edit_text(
            f"⛔ {target_id} забанен"
            + (" навсегда." if not hours else f" на {hours} ч.")
        )


@admin_router.callback_query(F.data.startswith("admin:dogrant:"))
async def cb_admin_dogrant(callback: CallbackQuery):
    parts = callback.data.split(":")
    target_id, days = int(parts[2]), int(parts[3])
    result = await AdminService().grant(
        callback.from_user.id,
        target_id,
        days,
        "Решение администратора",
        request_key=f"grant:{callback.message.chat.id}:{callback.message.message_id}",
    )
    await callback.answer("Premium выдан" if result.ok else "Не удалось выдать")
    if result.ok:
        await callback.message.edit_text(
            f'<tg-emoji emoji-id="5312160339335347417">🎁</tg-emoji> {target_id}: Premium на {days} дн.'
        )


@admin_router.message(
    Command("user", "ban_user", "unban", "grant", "mute_user", "unmute")
)
async def cmd_user_action(message: Message):
    parts = message.text.split(maxsplit=3)
    if len(parts) < 2:
        await message.answer("Укажи anon_id или telegram_id.")
        return
    user = await AdminService().find_user(message.from_user.id, parts[1])
    if not user:
        await message.answer("Пользователь не найден.")
        return
    command = parts[0].split("@")[0]
    if command == "/user":
        await message.answer(
            await user_card_text(message.from_user.id, user),
            reply_markup=admin_user_card_kb(user.telegram_id),
        )
        return
    if command == "/unban":
        result = await AdminService().unban(message.from_user.id, user.telegram_id)
    elif command == "/unmute":
        result = await AdminService().unmute(message.from_user.id, user.telegram_id)
    elif len(parts) == 4 and parts[2].isdigit():
        if command == "/grant":
            result = await AdminService().grant(
                message.from_user.id,
                user.telegram_id,
                int(parts[2]),
                parts[3],
                request_key=f"grant:{message.chat.id}:{message.message_id}",
            )
        elif command == "/mute_user":
            result = await AdminService().mute(
                message.from_user.id, user.telegram_id, parts[3], int(parts[2]) or None
            )
        else:
            result = await AdminService().ban(
                message.from_user.id, user.telegram_id, parts[3], int(parts[2]) or None
            )
    else:
        await message.answer("Укажи ID, длительность числом и причину.")
        return
    await message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Сохранено'
        if result.ok
        else "Проверь параметры."
    )


async def outside_chat(message: Message):
    return await get_partner(message.from_user.id) is None


@admin_router.message(
    StateFilter(None),
    F.from_user.id.func(lambda user_id: user_id in config.ADMIN_IDS),
    F.text.func(
        lambda text: (
            bool(text) and (text.strip().isdigit() or text.strip().startswith("#"))
        )
    ),
    outside_chat,
)
async def admin_text_search(message: Message):
    text = message.text.strip()
    if text.isdigit() or text.startswith("#"):
        user = await AdminService().find_user(message.from_user.id, text.lstrip("#"))
        if user:
            await message.answer(
                await user_card_text(message.from_user.id, user),
                reply_markup=admin_user_card_kb(user.telegram_id),
            )


def _setting_page(key: str) -> int:
    return SETTING_KEYS.index(key) // SETTING_PAGE_SIZE + 1


async def settings_page(admin_id, page: int):
    """(text, markup) for one page of the parameter catalog."""
    values = await AdminService().settings_all(admin_id)
    pages = max(1, (len(SETTING_KEYS) + SETTING_PAGE_SIZE - 1) // SETTING_PAGE_SIZE)
    page = min(max(page, 1), pages)
    start = (page - 1) * SETTING_PAGE_SIZE
    entries = [
        (key, SETTING_TITLES[key], values.get(key, SETTING_DEFAULTS[key]))
        for key in SETTING_KEYS[start : start + SETTING_PAGE_SIZE]
    ]
    text = (
        f'<tg-emoji emoji-id="6129805886383723340">⚙️</tg-emoji> Параметры бота · {page}/{pages}\n'
        "Нажми параметр, чтобы поставить значение пресетом или ввести своё."
    )
    return text, admin_settings_kb(entries, page, pages)


@admin_router.callback_query(F.data == "admin:settings")
@admin_router.callback_query(F.data.startswith("admin:setlist:"))
async def cb_settings(callback: CallbackQuery, state: FSMContext):
    """Paged list of every tunable parameter; the catalog lives in services.admin."""
    await state.clear()
    tail = callback.data.split(":")[-1]
    page = int(tail) if tail.isdigit() else 1
    await callback.answer()
    text, markup = await settings_page(callback.from_user.id, page)
    await callback.message.answer(text, reply_markup=markup)


@admin_router.message(Command("price", "setting"))
async def cmd_setting(message: Message):
    p = message.text.split()
    if len(p) != 3 or not p[2].isdigit():
        await message.answer("Нужны название параметра и числовое значение.")
        return
    service = AdminService()
    result = await (
        service.price(message.from_user.id, p[1], int(p[2]))
        if p[0].split("@")[0] == "/price"
        else service.setting(message.from_user.id, p[1], int(p[2]))
    )
    await message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Сохранено'
        if result.ok
        else "Недопустимое значение."
    )


STATUS_TITLES = {
    "draft": "черновик",
    "queued": "готовим список",
    "sending": "отправка",
    "completed": "готово",
    "cancelled": "остановлена вручную",
}
FILLED, EMPTY, THIN_SPACE = "█", "░", " "


def amount(value):
    return f"{value:,}".replace(",", THIN_SPACE)


def progress_bar(done, total, width=16):
    if total <= 0:
        return EMPTY * width
    filled = max(0, min(width, round(width * done / total)))
    return FILLED * filled + EMPTY * (width - filled)


def broadcast_card(job, counts, poll_totals=None):
    """One renderer for the live progress message and the status screen.

    poll_totals: campaign-wide vote sums (see NotificationService.poll_totals/
    progress_updates), aligned index-for-index with json.loads(job.poll_options).
    None until at least one recipient has voted; omitted entirely for text jobs.
    """
    sent, failed = counts.get("sent", 0), counts.get("failed", 0)
    skipped, expanded = counts.get("skipped", 0), sum(counts.values())
    processed = sent + failed + skipped
    # Until expansion finishes, the audience snapshot is the honest denominator.
    scale = max(job.total, expanded, 1)
    kind_note = " · опрос" if getattr(job, "kind", "text") == "poll" else ""
    lines = [
        '<tg-emoji emoji-id="5215668805199473901">📢</tg-emoji> '
        f"Рассылка #{job.id} · {SEGMENT_TITLES.get(job.segment, job.segment)}{kind_note}"
    ]
    if job.status == "draft":
        lines.append("\nЧерновик, ещё не запущена.")
    else:
        lines.append(
            f"\n<code>[{progress_bar(processed, scale)}]</code> {round(100 * processed / scale)}%\n"
        )
        lines.append(f"Отправлено: {amount(sent)}")
        blocked = f" (заблокировали бота: {amount(job.blocked)})" if job.blocked else ""
        lines.append(f"Не доставлено: {amount(failed)}{blocked}")
        if skipped:
            lines.append(f"Пропущено: {amount(skipped)}")
        lines.append(
            f"Обработано: {amount(processed)} из {amount(max(job.total, expanded))}"
        )
    lines.append(f"\nСтатус: {STATUS_TITLES.get(job.status, job.status)}")
    if job.started_at:
        lines.append(f"Запущена: {job.started_at:%d.%m.%Y %H:%M} UTC")
    if job.finished_at:
        lines.append(f"Завершена: {job.finished_at:%d.%m.%Y %H:%M} UTC")
    if getattr(job, "kind", "text") == "poll":
        if poll_totals:
            import json

            labels = json.loads(job.poll_options)
            voters = sum(poll_totals)
            lines.append(f"\n📊 Голосов: {amount(voters)}")
            lines += [
                f"{label} — {amount(count)}"
                for label, count in zip(labels, poll_totals)
            ]
        else:
            lines.append("\nГолосов пока нет.")
    return "\n".join(lines)


def broadcast_card_kb(job_id, status=None):
    rows = [
        [
            InlineKeyboardButton(
                text="Обновить",
                icon_custom_emoji_id="5260450573768990626",
                callback_data=f"admin:broadcasts:{job_id}",
            ),
            InlineKeyboardButton(text="К списку", callback_data="admin:broadcasts"),
        ]
    ]
    if status in {"queued", "sending"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⛔ Остановить",
                    callback_data=f"admin:bcancel:{job_id}",
                    style="danger",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast(callback: CallbackQuery):
    service = AdminService()
    sizes = {
        segment: await service.audience_size(callback.from_user.id, segment)
        for segment in BROADCAST_SEGMENTS
    }
    await callback.answer()
    await callback.message.answer(
        '<tg-emoji emoji-id="5215668805199473901">📢</tg-emoji> Выбери аудиторию:',
        reply_markup=admin_broadcast_audience_kb(sizes),
    )


@admin_router.callback_query(F.data.startswith("bc:"))
async def cb_audience(callback: CallbackQuery, state: FSMContext):
    audience = callback.data.split(":")[-1]
    if audience not in BROADCAST_SEGMENTS:
        await callback.answer("Неизвестная аудитория")
        return
    await state.set_state(AdminBroadcast.CONTENT)
    await state.update_data(audience=audience)
    await callback.answer()
    await callback.message.answer(
        "Введи текст. Перед отправкой будет предпросмотр и подтверждение."
    )


@admin_router.message(
    AdminBroadcast.CONTENT, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def broadcast_text(message: Message, state: FSMContext):
    audience = (await state.get_data()).get("audience", "all")
    result = await AdminService().broadcast(
        message.from_user.id, audience, message.text
    )
    if not result.ok:
        await message.answer("Текст должен содержать от 1 до 4000 символов.")
        return
    await state.clear()
    job = result.data["job"]
    await message.answer(
        f'<tg-emoji emoji-id="5253959125838090076">👁</tg-emoji> Предпросмотр · {audience}\n\n{message.text}',
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Запустить рассылку",
                        icon_custom_emoji_id="5188481279963715781",
                        callback_data=f"admin:send:{job.id}",
                        style="danger",
                    )
                ]
            ]
        ),
    )


@admin_router.poll()
async def poll_vote_update(poll):
    # Fires for ANY recipient's poll, not just admins -- AdminOnly middleware
    # is bound only to .message/.callback_query, never to .poll, so this must
    # not assume an admin is voting. A no-op for poll_ids we never sent.
    from services.notifications import NotificationService

    await NotificationService().update_poll(
        poll.id, [o.voter_count for o in poll.options]
    )


@admin_router.callback_query(F.data == "admin:poll")
async def cb_poll(callback: CallbackQuery):
    service = AdminService()
    sizes = {
        segment: await service.audience_size(callback.from_user.id, segment)
        for segment in BROADCAST_SEGMENTS
    }
    await callback.answer()
    await callback.message.answer(
        "📊 Выбери аудиторию для опроса:",
        reply_markup=admin_broadcast_audience_kb(sizes, poll=True),
    )


@admin_router.callback_query(F.data.startswith("bcp:"))
async def cb_audience_poll(callback: CallbackQuery, state: FSMContext):
    audience = callback.data.split(":")[-1]
    if audience not in BROADCAST_SEGMENTS:
        await callback.answer("Неизвестная аудитория")
        return
    await state.set_state(AdminBroadcast.POLL_QUESTION)
    await state.update_data(audience=audience)
    await callback.answer()
    await callback.message.answer("Введи текст вопроса (до 300 символов).")


@admin_router.message(
    AdminBroadcast.POLL_QUESTION,
    F.text,
    ~F.text.in_(T.SYSTEM_TEXTS),
    ~F.text.startswith("/"),
)
async def broadcast_poll_question(message: Message, state: FSMContext):
    if not 1 <= len(message.text) <= 300:
        await message.answer("Вопрос должен содержать от 1 до 300 символов.")
        return
    await state.update_data(question=message.text)
    await state.set_state(AdminBroadcast.POLL_OPTIONS)
    await message.answer(
        "Теперь варианты ответов — по одному на строку, от 2 до 10 штук, каждый до 100 символов."
    )


@admin_router.message(
    AdminBroadcast.POLL_OPTIONS,
    F.text,
    ~F.text.in_(T.SYSTEM_TEXTS),
    ~F.text.startswith("/"),
)
async def broadcast_poll_options(message: Message, state: FSMContext):
    options = [line.strip() for line in message.text.split("\n") if line.strip()]
    data = await state.get_data()
    audience, question = data.get("audience", "all"), data.get("question", "")
    result = await AdminService().broadcast(
        message.from_user.id, audience, question, kind="poll", poll_options=options
    )
    if not result.ok:
        await message.answer(
            "Нужно от 2 до 10 вариантов, каждый от 1 до 100 символов. Пришли варианты заново."
        )
        return
    await state.clear()
    job = result.data["job"]
    preview = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(options))
    await message.answer(
        f"👁 Предпросмотр опроса · {audience}\n\n{question}\n\n{preview}",
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Запустить рассылку",
                        icon_custom_emoji_id="5188481279963715781",
                        callback_data=f"admin:send:{job.id}",
                        style="danger",
                    )
                ]
            ]
        ),
    )


@admin_router.callback_query(F.data.startswith("admin:send:"))
async def cb_send(callback: CallbackQuery):
    value = callback.data.split(":")[-1]
    if not value.isdigit():
        await callback.answer("Недопустимая рассылка")
        return
    service = AdminService()
    result = await service.confirm_broadcast(callback.from_user.id, int(value))
    await callback.answer(
        "Добавлено в очередь" if result.ok else "Рассылка уже запущена"
    )
    if not result.ok:
        return
    job = result.data["job"]
    # The worker keeps editing this message until the broadcast finishes.
    progress = await callback.message.answer(
        broadcast_card(job, {}), reply_markup=broadcast_card_kb(job.id, job.status)
    )
    await service.attach_progress(
        callback.from_user.id, job.id, progress.chat.id, progress.message_id
    )


@admin_router.callback_query(F.data == "admin:broadcasts")
@admin_router.callback_query(F.data.startswith("admin:broadcasts:"))
async def cb_broadcast_status(callback: CallbackQuery):
    value = callback.data.split(":")[-1]
    job_id = int(value) if value.isdigit() else None
    if job_id:
        found = await AdminService().broadcast_progress(callback.from_user.id, job_id)
        if not found:
            await callback.answer("Рассылка не найдена", show_alert=True)
            return
        job, counts = found
        poll_totals = None
        if job.kind == "poll":
            from services.notifications import NotificationService

            poll_totals = await NotificationService().poll_totals(job.id)
        text, markup = (
            broadcast_card(job, counts, poll_totals),
            broadcast_card_kb(job.id, job.status),
        )
        try:
            await callback.message.edit_text(text, reply_markup=markup)
            await callback.answer("Обновлено")
        except TelegramBadRequest as error:
            # Refreshing an unchanged card is normal; anything else gets a new message.
            if "message is not modified" in error.message:
                await callback.answer("Без изменений")
            else:
                await callback.answer()
                await callback.message.answer(text, reply_markup=markup)
    else:
        jobs = await AdminService().broadcast_status(callback.from_user.id)
        await callback.answer()
        if not jobs:
            await callback.message.answer(
                '<tg-emoji emoji-id="5215668805199473901">📢</tg-emoji> Рассылок пока нет.',
                reply_markup=admin_back_kb("admin:sec:broadcast"),
            )
            return
        await callback.message.answer(
            '<tg-emoji emoji-id="5215668805199473901">📢</tg-emoji> Последние рассылки:',
            reply_markup=admin_broadcast_status_kb(jobs),
        )


def ad_campaign_card_text(campaign):
    from handlers.chat import ad_card_text

    preview = ad_card_text(
        {
            "title": campaign.title,
            "subtitle": campaign.subtitle,
            "body": campaign.body,
            "channel_username": campaign.channel_username,
        }
    )

    def limit(v):
        return "без лимита" if not v else str(v)

    joins = (
        f"Вступлений по рекламной ссылке: {campaign.joins_total}"
        if campaign.invite_link
        else "Вступления не отслеживаются — добавь бота админом канала и перезапусти кампанию."
    )
    stats = (
        f"\n\n<b>Кампания #{campaign.id}</b>\n"
        f"Канал: @{campaign.channel_username}\n"
        f"Показов: {campaign.impressions_total} · дневной лимит: {limit(campaign.daily_limit)} · "
        f"общий лимит: {limit(campaign.total_limit)}\n"
        f"{joins}\n"
        f"Клики не измеряются — Telegram не уведомляет бота о нажатии ссылки."
    )
    return preview + stats


@admin_router.callback_query(F.data == "admin:ads")
@admin_router.callback_query(F.data == "admin:sec:ads")
async def cb_ads(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    campaigns = await AdCampaignService().list(callback.from_user.id)
    await callback.answer()
    text = (
        '<tg-emoji emoji-id="5215668805199473901">📢</tg-emoji> Рекламные кампании'
        if campaigns
        else '<tg-emoji emoji-id="5215668805199473901">📢</tg-emoji> Кампаний пока нет.'
    )
    await callback.message.answer(text, reply_markup=admin_ads_kb(campaigns))


async def ads_settings_text(cooldown):
    minutes = cooldown // 60
    return (
        f'<tg-emoji emoji-id="6129805886383723340">⚙️</tg-emoji> Настройки рекламы\n\n'
        f"Частота показа: не чаще раза в {minutes} мин ({cooldown} сек) одному пользователю.\n"
        f"Действует сразу на все кампании."
    )


@admin_router.callback_query(F.data == "admin:ads:settings")
async def cb_ads_settings(callback: CallbackQuery, state: FSMContext):
    from services.settings import bot_value

    await state.clear()
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await callback.answer()
    await callback.message.answer(
        await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown)
    )


@admin_router.callback_query(F.data.startswith("admin:ads:settings:set:"))
async def cb_ads_settings_set(callback: CallbackQuery):
    from services.settings import bot_value

    value = callback.data.split(":")[-1]
    if not value.isdigit():
        await callback.answer("Недопустимое значение")
        return
    result = await AdminService().setting(
        callback.from_user.id, "ad_cooldown_seconds", int(value)
    )
    await callback.answer("Сохранено" if result.ok else "Недопустимое значение")
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await callback.message.edit_text(
        await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown)
    )


@admin_router.callback_query(F.data == "admin:ads:settings:custom")
async def cb_ads_settings_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminAdSettings.COOLDOWN)
    await callback.answer()
    await callback.message.answer(
        "Введи новую частоту показа в секундах (0–86400, например 900):",
        reply_markup=admin_ads_settings_cancel_kb(),
    )


@admin_router.callback_query(F.data == "admin:ads:settings:cancel")
async def cb_ads_settings_cancel(callback: CallbackQuery, state: FSMContext):
    from services.settings import bot_value

    await state.clear()
    await callback.answer("Отменено")
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await callback.message.answer(
        await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown)
    )


@admin_router.message(
    AdminAdSettings.COOLDOWN,
    F.text,
    ~F.text.in_(T.SYSTEM_TEXTS),
    ~F.text.startswith("/"),
)
async def ads_settings_cooldown(message: Message, state: FSMContext):
    from services.settings import bot_value

    if not message.text.isdigit():
        await message.answer(
            "Нужно неотрицательное число секунд.",
            reply_markup=admin_ads_settings_cancel_kb(),
        )
        return
    result = await AdminService().setting(
        message.from_user.id, "ad_cooldown_seconds", int(message.text)
    )
    if not result.ok:
        await message.answer(
            "Значение вне диапазона 0–86400.",
            reply_markup=admin_ads_settings_cancel_kb(),
        )
        return
    await state.clear()
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Сохранено.'
    )
    await message.answer(
        await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown)
    )


@admin_router.message(
    Command("cancel"),
    StateFilter(
        AdminAdCampaign.CHANNEL,
        AdminAdCampaign.TITLE,
        AdminAdCampaign.SUBTITLE,
        AdminAdCampaign.BODY,
        AdminAdCampaign.LIMITS,
    ),
)
async def ad_campaign_cancel(message: Message, state: FSMContext):
    # The first prompt in this flow (cb_ad_new below) tells the admin to send
    # /cancel to bail out -- but every step's message filter excludes text
    # starting with "/", so that command was silently swallowed with no
    # handler and no way out short of finishing all five steps.
    await state.clear()
    await message.answer(
        "Создание рекламной кампании отменено.", reply_markup=admin_kb()
    )


@admin_router.callback_query(F.data == "admin:ad:new")
async def cb_ad_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminAdCampaign.CHANNEL)
    await callback.answer()
    await callback.message.answer(
        "Username канала или группы-рекламодателя, с @ (например @anon_chat_news):\n/cancel — отменить."
    )


@admin_router.message(
    AdminAdCampaign.CHANNEL,
    F.text,
    ~F.text.in_(T.SYSTEM_TEXTS),
    ~F.text.startswith("/"),
)
async def ad_channel(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.startswith("@"):
        await message.answer(
            "Нужно с @ в начале, например @anon_chat_news. Попробуй ещё раз."
        )
        return
    value = raw.lstrip("@")
    if not CHANNEL_USERNAME_RE.fullmatch(value):
        await message.answer(
            "Username: 5–32 символа после @, латиница/цифры/подчёркивание. Попробуй ещё раз."
        )
        return
    await state.update_data(channel_username=value)
    await state.set_state(AdminAdCampaign.TITLE)
    await message.answer(
        "Название с эмодзи, до 64 символов (например «🌍 Мир вокруг»):"
    )


@admin_router.message(
    AdminAdCampaign.TITLE, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def ad_title(message: Message, state: FSMContext):
    if not 1 <= len(message.text) <= 64:
        await message.answer("Название должно быть от 1 до 64 символов.")
        return
    await state.update_data(title=message.text)
    await state.set_state(AdminAdCampaign.SUBTITLE)
    await message.answer(
        "Подзаголовок, до 64 символов (например «Путешествия · 89K подписчиков»). Пришли «-», чтобы пропустить:"
    )


@admin_router.message(
    AdminAdCampaign.SUBTITLE,
    F.text,
    ~F.text.in_(T.SYSTEM_TEXTS),
    ~F.text.startswith("/"),
)
async def ad_subtitle(message: Message, state: FSMContext):
    if len(message.text) > 64:
        await message.answer("Подзаголовок должен быть не длиннее 64 символов.")
        return
    await state.update_data(
        subtitle="" if message.text.strip() == "-" else message.text
    )
    await state.set_state(AdminAdCampaign.BODY)
    await message.answer("Текст объявления, до 400 символов:")


@admin_router.message(
    AdminAdCampaign.BODY, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def ad_body(message: Message, state: FSMContext):
    if not 1 <= len(message.text) <= 400:
        await message.answer("Текст объявления должен быть от 1 до 400 символов.")
        return
    await state.update_data(body=message.text)
    await state.set_state(AdminAdCampaign.LIMITS)
    await message.answer(
        "Лимиты показов: два числа через пробел — дневной и общий (0 = без лимита). Например «0 5000»:"
    )


@admin_router.message(
    AdminAdCampaign.LIMITS, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def ad_limits(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer(
            "Нужны два неотрицательных числа через пробел, например «0 5000»."
        )
        return
    data = await state.get_data()
    result = await AdCampaignService().create(
        message.from_user.id,
        data["channel_username"],
        data["title"],
        data["subtitle"],
        data["body"],
        int(parts[0]),
        int(parts[1]),
    )
    await state.clear()
    if not result.ok:
        await message.answer(f"Не удалось создать кампанию: {result.code}")
        return
    campaign = await AdCampaignService().get(
        message.from_user.id, result.data["campaign_id"]
    )
    await message.answer(
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Черновик сохранён. Так увидят объявление подписчики:'
    )
    await message.answer(
        ad_campaign_card_text(campaign), reply_markup=admin_ad_card_kb(campaign)
    )


def _ad_id(callback):
    parts = callback.data.split(":")
    return int(parts[-1]) if parts[-1].isdigit() else None


@admin_router.callback_query(F.data.startswith("admin:ad:launch:"))
async def cb_ad_launch(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    if campaign_id is None:
        await callback.answer("Недопустимая кампания")
        return
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id)
    if not campaign:
        await callback.answer("Кампания не найдена")
        return
    invite_link, note = None, ""
    try:
        # A tracked invite link is what lets a later join be attributed to
        # this campaign; it only succeeds if the bot is an admin there.
        link = await callback.bot.create_chat_invite_link(
            chat_id=f"@{campaign.channel_username}", name=f"ad-{campaign.id}"
        )
        invite_link = link.invite_link
    except (TelegramBadRequest, TelegramForbiddenError):
        note = (
            "\n\n⚠️ Ссылка для учёта вступлений не создана: бот не админ канала "
            f"@{campaign.channel_username}. Кампания запущена с обычной ссылкой; "
            "добавь бота админом и перезапусти, если нужна статистика вступлений."
        )
    result = await AdCampaignService().set_status(
        callback.from_user.id, campaign_id, "active", invite_link=invite_link
    )
    await callback.answer("Запущено" if result.ok else "Действие недоступно")
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id)
    if campaign:
        await callback.message.answer(
            ad_campaign_card_text(campaign) + note,
            reply_markup=admin_ad_card_kb(campaign),
        )


@admin_router.callback_query(F.data.startswith("admin:ad:pause:"))
@admin_router.callback_query(F.data.startswith("admin:ad:resume:"))
@admin_router.callback_query(F.data.startswith("admin:ad:complete:"))
async def cb_ad_status(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    action = callback.data.split(":")[2]
    status = {"pause": "paused", "resume": "active", "complete": "completed"}[action]
    if campaign_id is None:
        await callback.answer("Недопустимая кампания")
        return
    result = await AdCampaignService().set_status(
        callback.from_user.id, campaign_id, status
    )
    await callback.answer("Сохранено" if result.ok else "Действие недоступно")
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id)
    if campaign:
        await callback.message.answer(
            ad_campaign_card_text(campaign), reply_markup=admin_ad_card_kb(campaign)
        )


@admin_router.callback_query(F.data.startswith("admin:ad:delete:"))
async def cb_ad_delete(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    if campaign_id is None:
        await callback.answer("Недопустимая кампания")
        return
    result = await AdCampaignService().delete_draft(callback.from_user.id, campaign_id)
    await callback.answer("Удалено" if result.ok else "Действие недоступно")
    if result.ok:
        campaigns = await AdCampaignService().list(callback.from_user.id)
        await callback.message.answer(
            "Черновик удалён.", reply_markup=admin_ads_kb(campaigns)
        )


@admin_router.callback_query(F.data.regexp(r"^admin:ad:\d+$"))
async def cb_ad_view(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    campaign = (
        await AdCampaignService().get(callback.from_user.id, campaign_id)
        if campaign_id is not None
        else None
    )
    if not campaign:
        await callback.answer("Кампания не найдена", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        ad_campaign_card_text(campaign), reply_markup=admin_ad_card_kb(campaign)
    )


# ── Sectioned panel ──────────────────────────────────────────────
# Root opens sections, sections open screens, every screen's «Назад»
# returns to its own section and every section to the root: the whole
# panel is two taps from anywhere.

ADMIN_SECTIONS = {
    "admin:sec:analytics": (
        "📊 Аналитика",
        admin_analytics_kb,
        "Статистика и монетизация, воронка регистрации, рост и возвраты, события, выгрузка CSV.",
    ),
    "admin:sec:users": (
        "👥 Пользователи",
        admin_users_section_kb,
        "Поиск по ID, рейтинг покупок и свежие регистрации -- каждая строка открывает карточку.",
    ),
    "admin:sec:moderation": (
        "🛡 Модерация",
        admin_moderation_kb,
        "Жалобы в очереди, активные баны и сезонное событие.",
    ),
    "admin:sec:ideas": (
        "💡 Предложения",
        lambda: admin_ideas_kb(),
        "Приём идей: фильтр по статусу, карточки, ответы авторам и дубликаты.",
    ),
    "admin:sec:broadcast": (
        "📨 Рассылки",
        admin_broadcast_section_kb,
        "Новая волна или опрос по сегменту, статусы запущенных и их остановка.",
    ),
    "admin:sec:settings": (
        "⚙️ Настройки",
        admin_settings_section_kb,
        "Цены на продукты, параметры бота и частота показа рекламы.",
    ),
    "admin:sec:system": (
        "🧰 Система",
        admin_system_kb,
        "Сердцебиение воркера, база, Redis, очередь и журнал действий администраторов.",
    ),
}


@admin_router.callback_query(F.data.in_(tuple(ADMIN_SECTIONS)))
async def cb_section(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    title, keyboard, summary = ADMIN_SECTIONS[callback.data]
    await callback.message.answer(f"{title}\n\n{summary}", reply_markup=keyboard())


@admin_router.callback_query(F.data.startswith("admin:growth:"))
async def cb_growth(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    tail = callback.data.split(":")[-1]
    days = int(tail) if tail.isdigit() else 1
    if days not in {1, 7, 30}:
        await callback.answer("Недопустимый период.")
        return
    await callback.answer()
    g = await StatsService().growth(days)
    await safe_send(
        callback.message,
        growth_text(g, days) + f"\n🟢 Онлайн: {await get_online_count()}",
        reply_markup=admin_growth_kb(days),
    )


@admin_router.callback_query(F.data == "admin:top")
async def cb_top(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    users = await AdminService().top_spenders(callback.from_user.id, 15)
    await callback.answer()
    if not users:
        await callback.message.answer(
            "Покупок пока не было.", reply_markup=admin_back_kb("admin:sec:users")
        )
        return
    lines = [
        f"{i}. {escape(u.anon_id or '?')} -- {u.total_stars_spent} ⭐"
        for i, u in enumerate(users, 1)
    ]
    await callback.message.answer(
        "🏆 Топ по тратам\n\n" + "\n".join(lines),
        reply_markup=admin_user_list_kb(
            [
                (
                    u.telegram_id,
                    f"{escape(u.anon_id or '?')} -- {u.total_stars_spent} ⭐",
                )
                for u in users
            ],
            "admin:sec:users",
        ),
    )


@admin_router.callback_query(F.data == "admin:recent")
async def cb_recent(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    users = await AdminService().recent_users(callback.from_user.id, 15)
    await callback.answer()
    if not users:
        await callback.message.answer(
            "Новых пользователей нет.", reply_markup=admin_back_kb("admin:sec:users")
        )
        return
    lines = [
        f"{escape(u.anon_id or '?')} · {u.created_at:%d.%m %H:%M} UTC" for u in users
    ]
    await callback.message.answer(
        "🆕 Новые пользователи\n\n" + "\n".join(lines),
        reply_markup=admin_user_list_kb(
            [
                (
                    u.telegram_id,
                    f"{escape(u.anon_id or '?')} · {u.created_at:%d.%m %H:%M}",
                )
                for u in users
            ],
            "admin:sec:users",
        ),
    )


AUDIT_PAGE = 10


@admin_router.callback_query(F.data.startswith("admin:audit:"))
async def cb_audit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    tail = callback.data.split(":")[-1]
    offset = int(tail) if tail.isdigit() else 0
    rows = await AdminService().audit_log(callback.from_user.id, offset, AUDIT_PAGE)
    await callback.answer()
    if not rows:
        await callback.message.answer(
            "Журнал действий пуст.",
            reply_markup=admin_audit_kb(offset, AUDIT_PAGE, False),
        )
        return
    lines = []
    for row in rows:
        stamp = row.created_at.strftime("%d.%m %H:%M")
        target = escape(f"{row.target_type}:{row.target_id}")
        detail = ""
        if row.after_json:
            detail = " · " + ", ".join(
                f"{escape(str(k))}={escape(str(v))}"
                for k, v in list(row.after_json.items())[:4]
            )
        lines.append(
            f"#{row.id} {stamp} · admin {row.admin_id} · {escape(row.action)} · {target}{detail}"
        )
    await callback.message.answer(
        "📜 Журнал действий\n\n" + "\n".join(lines),
        reply_markup=admin_audit_kb(offset, AUDIT_PAGE, len(rows) == AUDIT_PAGE),
    )


@admin_router.callback_query(F.data == "admin:syshealth")
async def cb_syshealth(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    live = await AdminService().system_status(callback.from_user.id)
    await callback.answer()
    beat = live["heartbeat_age"]
    if beat is None:
        heartbeat = "нет отметки -- воркер не поднимался"
    elif beat > 120:
        heartbeat = f"⚠️ {beat:.0f} с назад -- молчит"
    else:
        heartbeat = f"ок, {beat:.1f} с назад"
    text = "\n".join(
        [
            "🧰 Система",
            "",
            f"Сердцебиение воркера: {heartbeat}",
            f"PostgreSQL: {'ок' if live['db'] else 'недоступна'}",
            f"Redis: {'ок' if live['redis'] else 'недоступен'}",
            f"В очереди: {amount(live['queued'])} · активных рассылок: {live['broadcasts_active']}",
            f"Жалоб в очереди: {live['reports_pending']}",
            f"Пользователей: {amount(live['users'])} · онлайн: {live['online']}",
        ]
    )
    await callback.message.answer(text, reply_markup=admin_back_kb("admin:sec:system"))


@admin_router.callback_query(F.data == "admin:prices")
async def cb_prices(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        "💰 Цены на продукты\n\nНажми продукт, чтобы ввести новую цену в Stars.",
        reply_markup=admin_prices_kb(await PaymentService().products()),
    )


@admin_router.callback_query(F.data.startswith("admin:price:"))
async def cb_price_edit(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 2)[-1]
    product = next(
        (p for p in await PaymentService().products() if p.code == code), None
    )
    if product is None:
        await callback.answer("Продукт не найден", show_alert=True)
        return
    await state.set_state(AdminPriceValue.VALUE)
    await state.update_data(code=code)
    await callback.answer()
    await callback.message.answer(
        f"{product.title}\nСейчас: {product.price_stars} ⭐\n\n"
        "Пришли новую цену целым числом Stars или /cancel."
    )


@admin_router.message(
    AdminPriceValue.VALUE, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/")
)
async def price_value_input(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("Нужно целое число Stars.")
        return
    code = (await state.get_data()).get("code", "")
    result = await AdminService().price(message.from_user.id, code, int(raw))
    await state.clear()
    if not result.ok:
        await message.answer("Недопустимая цена.")
        return
    await message.answer(
        "✅ Цена обновлена.",
        reply_markup=admin_prices_kb(await PaymentService().products()),
    )


async def render_setting(target, admin_id, key):
    """The editor screen for one parameter; target is a callback or a message."""
    values = await AdminService().settings_all(admin_id)
    current = values.get(key, SETTING_DEFAULTS[key])
    low, high = SETTING_LIMITS[key]
    await target.answer(
        f"{SETTING_TITLES[key]}\n\nСейчас: {current}\nДопустимо: {low}–{high}\n\n"
        "Выбери значение кнопкой или введи своё.",
        reply_markup=admin_setting_kb(
            key, _setting_page(key), SETTING_PRESETS[key], current
        ),
    )


@admin_router.callback_query(F.data.startswith("admin:setv:"))
async def cb_setting_edit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    key = callback.data.split(":", 2)[-1]
    if key not in SETTING_LIMITS:
        await callback.answer("Неизвестный параметр", show_alert=True)
        return
    await callback.answer()
    await render_setting(callback, callback.from_user.id, key)


@admin_router.callback_query(F.data.startswith("admin:set:"))
async def cb_setting_apply(callback: CallbackQuery):
    parts = callback.data.split(":")
    key = parts[2] if len(parts) > 2 else ""
    raw = parts[3] if len(parts) > 3 else ""
    if key not in SETTING_LIMITS or not raw.lstrip("-").isdigit():
        await callback.answer("Недопустимое значение", show_alert=True)
        return
    result = await AdminService().setting(callback.from_user.id, key, int(raw))
    if not result.ok:
        await callback.answer("Значение вне диапазона", show_alert=True)
        return
    await callback.answer("Сохранено")
    await render_setting(callback, callback.from_user.id, key)


@admin_router.callback_query(F.data.startswith("admin:setc:"))
async def cb_setting_custom(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    key = callback.data.split(":", 2)[-1]
    if key not in SETTING_LIMITS:
        await callback.answer("Неизвестный параметр", show_alert=True)
        return
    low, high = SETTING_LIMITS[key]
    await state.set_state(AdminSettingValue.VALUE)
    await state.update_data(key=key)
    await callback.answer()
    await callback.message.answer(
        f"Пришли значение для «{SETTING_TITLES[key]}» (от {low} до {high}) или /cancel."
    )


@admin_router.message(
    AdminSettingValue.VALUE,
    F.text,
    ~F.text.in_(T.SYSTEM_TEXTS),
    ~F.text.startswith("/"),
)
async def setting_value_input(message: Message, state: FSMContext):
    key = (await state.get_data()).get("key", "")
    if key not in SETTING_LIMITS:
        await state.clear()
        await message.answer("Параметр не найден.")
        return
    raw = message.text.strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("Нужно целое число.")
        return
    result = await AdminService().setting(message.from_user.id, key, int(raw))
    await state.clear()
    if not result.ok:
        low, high = SETTING_LIMITS[key]
        await message.answer(f"Значение вне диапазона {low}–{high}.")
        return
    await message.answer("✅ Сохранено.")
    text, markup = await settings_page(message.from_user.id, _setting_page(key))
    await message.answer(text, reply_markup=markup)


@admin_router.message(
    Command("cancel"), StateFilter(AdminSettingValue.VALUE, AdminPriceValue.VALUE)
)
async def cancel_value_input(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=admin_back_kb("admin:sec:settings"))


@admin_router.callback_query(F.data.startswith("admin:bcancel:"))
async def cb_broadcast_cancel(callback: CallbackQuery):
    parts = callback.data.split(":")
    job_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    if job_id is None:
        await callback.answer("Недопустимая рассылка", show_alert=True)
        return
    if len(parts) > 3 and parts[3] == "ok":
        result = await AdminService().cancel_broadcast(callback.from_user.id, job_id)
        if not result.ok:
            await callback.answer("Остановить нельзя", show_alert=True)
            return
        await callback.answer("Остановлена")
        found = await AdminService().broadcast_progress(callback.from_user.id, job_id)
        if found:
            job, counts = found
            await callback.message.answer(
                broadcast_card(job, counts),
                reply_markup=broadcast_card_kb(job.id, job.status),
            )
        return
    # First tap: confirm, because queued messages cannot be recalled afterwards.
    await callback.answer()
    await callback.message.answer(
        f"Остановить рассылку #{job_id}? Оставшиеся сообщения не отправятся, "
        "уже отправленные отозвать нельзя.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, остановить",
                        callback_data=f"admin:bcancel:{job_id}:ok",
                        style="danger",
                    ),
                    InlineKeyboardButton(
                        text="Назад", callback_data=f"admin:broadcasts:{job_id}"
                    ),
                ]
            ]
        ),
    )


# ── Идеи: раздел панели ──────────────────────────────────────────
# Список с фильтром-статусом и paging, карточка с контролируемыми
# переходами, ответ автору и отметка дубликата. Пользовательский
# текст в разметке панели присутствует только через escape().

from services.ideas import (
    IDEA_TRANSITIONS,
    IdeaService,
    status_label as idea_status_label,
)

IDEA_FILTER_LABELS = dict(IDEA_FILTERS)

# Отклонение - единственный шаг с подтверждением: уведомление автору
# не отзывается, так что случайный тап не должен его отправлять.
IDEA_REJECT_PROMPT = "Отклонить идею #{id}? Автор получит уведомление об отклонении."
IDEA_REPLY_LIMIT = 2000


def _idea_short(text: str, size: int = 48) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= size else line[: size - 1] + "…"


def _kyiv_stamp(when) -> str:
    from utils.time import KYIV

    return when.astimezone(KYIV).strftime("%d.%m.%Y %H:%M") if when else "—"


def _idea_entry_button(idea) -> tuple[int, str]:
    # В кнопках разметки нет: escape-сущности там выглядели бы буквами.
    return idea.id, f"#{idea.id} · {_idea_short(idea.text)}"


@admin_router.callback_query(F.data.regexp(r"^admin:ideas:(\w+):(\d+)$"))
async def cb_ideas_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    status, page = parts[2], parts[3]
    if status not in IDEA_FILTER_LABELS or not page.isdigit():
        await callback.answer("Неизвестный фильтр.")
        return
    rows, page, pages = await IdeaService().admin_list(status, int(page))
    await callback.answer()
    heading = (
        f"💡 <b>Предложения · {IDEA_FILTER_LABELS[status]}</b> · стр. {page}/{pages}"
    )
    if rows:
        lines = "\n".join(
            f"#{idea.id} · {escape(_idea_short(idea.text))} · "
            f"{idea_status_label(idea.status, 'ru')} · <i>{_kyiv_stamp(idea.created_at)}</i>"
            for idea in rows
        )
        text = f"{heading}\n\n{lines}"
    else:
        text = f"{heading}\n\nВ этом статусе пока пусто."
    entries = [_idea_entry_button(idea) for idea in rows]
    await callback.message.answer(
        text, reply_markup=admin_ideas_kb(status, page, pages, entries)
    )


async def _author_line(idea) -> str:
    from repositories.users import UserRepository
    from utils.bots import label as bot_label

    author = await UserRepository().get(idea.telegram_user_id)
    name = "?" if author is None else escape(author.first_name or "?")
    if author is not None and author.username:
        name = f"{name} @{author.username}"
    return f"{name} · ТГ {idea.telegram_user_id} · {bot_label(idea.bot_id)}"


@admin_router.callback_query(F.data.regexp(r"^admin:idea:(\d+)$"))
async def cb_idea_card(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    idea = await IdeaService().get(int(callback.data.split(":")[2]))
    await callback.answer()
    if idea is None:
        await callback.message.answer(
            "Идея не найдена — возможно, удалена.", reply_markup=back_kb()
        )
        return
    await _send_idea_card(callback, idea)


async def _idea_card_text(idea) -> str:
    sim = await IdeaService().similar_count(idea.id, idea.text)
    dup_line = f"\n↩ Дубликат идеи #{idea.duplicate_of}" if idea.duplicate_of else ""
    reply_line = ""
    if idea.admin_reply:
        reply_line = (
            f"\n\n✉ <b>Ответ автору</b> ({_kyiv_stamp(idea.admin_reply_at)}):\n"
            f"{escape(idea.admin_reply)}"
        )
    return (
        f"💡 <b>Предложение #{idea.id}</b>\n\n{idea_status_label(idea.status, 'ru')}\n\n"
        f"<b>Описание:</b>\n{escape(idea.text)}\n\n"
        f"Автор: {await _author_line(idea)}\n"
        f"Отправлено: {_kyiv_stamp(idea.created_at)} · изменено: {_kyiv_stamp(idea.updated_at)}\n"
        f"Похожих (текст 1:1): {sim}{reply_line}{dup_line}"
    )


async def _send_idea_card(callback: CallbackQuery, idea):
    text = await _idea_card_text(idea)
    await callback.message.answer(text, reply_markup=_idea_card_kb(idea))


def _idea_card_kb(idea):
    allowed = sorted(IDEA_TRANSITIONS[idea.status])
    rows = []
    if allowed:
        rows.append(
            [
                InlineKeyboardButton(
                    text=idea_status_label(s, "ru"),
                    callback_data=("admin:idea:%d:reject" % idea.id)
                    if s == "rejected"
                    else f"admin:idea:{idea.id}:st:{s}",
                    style="primary" if s == "review" else None,
                )
                for s in allowed
            ]
        )
    if "duplicate" in allowed:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔁 Отметить дубликатом",
                    callback_data=f"admin:idea:{idea.id}:duplicate",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✉️ Ответить автору",
                callback_data=f"admin:idea:{idea.id}:reply",
                style="primary",
            ),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin:ideas:new:1")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_router.callback_query(F.data.regexp(r"^admin:idea:(\d+):reject$"))
async def cb_idea_reject_confirm(callback: CallbackQuery, state: FSMContext):
    idea_id = int(callback.data.split(":")[2])
    await callback.answer()
    await callback.message.answer(
        IDEA_REJECT_PROMPT.format(id=idea_id),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Да, отклонить",
                        callback_data=f"admin:idea:{idea_id}:st:rejected",
                        style="danger",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Не отклонять, вернуться",
                        callback_data=f"admin:idea:{idea_id}",
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(F.data.regexp(r"^admin:idea:(\d+):st:(\w+)$"))
async def cb_idea_status(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    idea_id, new_status = int(parts[2]), parts[4]
    if new_status not in IDEA_TRANSITIONS:
        await callback.answer("Неизвестный статус.")
        return
    result = await IdeaService().set_status(callback.from_user.id, idea_id, new_status)
    if result.code == "FORBID":
        await callback.answer(
            f"Из статуса «{idea_status_label(result.data['from'], 'ru')}» этот шаг запрещён."
        )
        return
    if result.code == "UNCHANGED":
        await callback.answer("Статус уже такой — уведомление не отправляла.")
        return
    if result.code == "BAD_STATUS":
        await callback.answer("Неизвестный статус.")
        return
    idea = await IdeaService().get(idea_id)
    if idea is None:
        await callback.answer("Идея не найдена.")
        return
    await callback.answer("Статус изменён, уведомление уйдёт из очереди.")
    await _send_idea_card(callback, idea)


@admin_router.callback_query(F.data.regexp(r"^admin:idea:(\d+):reply$"))
async def cb_idea_reply(callback: CallbackQuery, state: FSMContext):
    idea_id = int(callback.data.split(":")[2])
    if await IdeaService().get(idea_id) is None:
        await callback.answer("Идея не найдена.")
        return
    await state.clear()
    await state.set_state(AdminIdeaReply.TEXT)
    await state.set_data({"idea_reply_id": idea_id, "idea_reply_text": None})
    await callback.answer()
    await callback.message.answer(
        "✉ Напиши ответ автору одним сообщением — он получит его как уведомление.\n\n"
        "Отмена — /cancel или кнопкой.",
        reply_markup=back_kb(f"admin:idea:{idea_id}"),
    )


@admin_router.message(AdminIdeaReply.TEXT, F.text)
async def idea_reply_text(message: Message, state: FSMContext):
    data = await state.get_data()
    idea_id = data.get("idea_reply_id")
    draft = (message.text or "").strip()
    if not idea_id or not draft:
        await message.answer("Ответ пуст — напиши текст или /cancel.")
        return
    if len(draft) > IDEA_REPLY_LIMIT:
        await message.answer(
            f"Длиннее лимита ({len(draft)} из {IDEA_REPLY_LIMIT}). Сократи."
        )
        return
    await state.update_data({"idea_reply_text": draft})
    await message.answer(
        f"✉ Проверь ответ автору:\n\n{escape(draft)}\n\nОтправляем?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить ответ",
                        callback_data=f"admin:idea:{idea_id}:reply:send",
                        style="primary",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Переписать", callback_data=f"admin:idea:{idea_id}:reply"
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(F.data.regexp(r"^admin:idea:(\d+):reply:send$"))
async def cb_idea_reply_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    reply_text = data.get("idea_reply_text") or ""
    idea_id = int(callback.data.split(":")[2])
    if not reply_text:
        await callback.answer("Ответ ещё не написан — напиши его сообщением.")
        return
    result = await IdeaService().save_reply(callback.from_user.id, idea_id, reply_text)
    await state.clear()
    if result.ok:
        await callback.answer("Ответ сохранён, автор получит уведомление из очереди.")
        idea = await IdeaService().get(idea_id)
        if idea is not None:
            await _send_idea_card(callback, idea)
        return
    await callback.answer(
        {
            "NOT_FOUND": "Идея не найдена.",
            "BAD_REPLY": "Ответ пуст или слишком длинный.",
        }.get(result.code, "Не сохранилось — повтори.")
    )


@admin_router.callback_query(F.data.regexp(r"^admin:idea:(\d+):duplicate$"))
async def cb_idea_duplicate(callback: CallbackQuery, state: FSMContext):
    idea_id = int(callback.data.split(":")[2])
    if await IdeaService().get(idea_id) is None:
        await callback.answer("Идея не найдена.")
        return
    await state.clear()
    await state.set_state(AdminIdeaDuplicate.ORIGINAL)
    await state.set_data({"idea_dup_id": idea_id})
    await callback.answer()
    await callback.message.answer(
        "🔁 Отправь номер исходной идеи цифрами. Если оригинала нет — отправь 0.",
        reply_markup=back_kb(f"admin:idea:{idea_id}"),
    )


@admin_router.message(AdminIdeaDuplicate.ORIGINAL, F.text)
async def idea_duplicate_text(message: Message, state: FSMContext):
    data = await state.get_data()
    idea_id = data.get("idea_dup_id")
    raw = (message.text or "").replace("#", "").strip()
    if not idea_id or not raw.isdigit():
        await message.answer("Нужен номер идеи цифрами (или 0). /cancel — отмена.")
        return
    original = int(raw) or None
    if original is not None and await IdeaService().get(original) is None:
        await message.answer(
            f"Идеи #{original} нет в базе. Проверь номер или отправь 0."
        )
        return
    result = await IdeaService().set_status(
        message.from_user.id, idea_id, "duplicate", duplicate_of=original
    )
    await state.clear()
    if not result.ok:
        reason = {
            "FORBID": f"Из статуса «{idea_status_label(result.data.get('from', 'new'), 'ru')}» "
            "нельзя уйти в дубликаты.",
            "UNCHANGED": "Статус и так дубликат.",
        }.get(result.code, "Не получилось.")
        await message.answer(reason, reply_markup=back_kb(f"admin:idea:{idea_id}"))
        return
    idea = await IdeaService().get(idea_id)
    if idea is None:
        await message.answer("Отмечено дубликатом.", reply_markup=back_kb())
        return
    await message.answer(
        "🔁 Отмечено дубликатом, автор уведомлён. Карточка:",
        reply_markup=_idea_card_kb(idea),
    )
