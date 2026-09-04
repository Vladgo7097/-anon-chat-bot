import asyncio
from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import admin_router
from keyboards.inline import admin_kb, admin_report_kb, admin_broadcast_audience_kb
from utils.db import (
    get_total_users, get_new_users_today, get_total_chats_today,
    get_total_stars_today, get_premium_count, get_pending_reports,
    resolve_report, ban_user, get_user_by_anon_id,
)
from services.chat_manager import get_online_count, get_search_queue_length
from config import ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@admin_router.message(F.command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    total = await get_total_users()
    online = await get_online_count()
    chats_today = await get_total_chats_today()
    stars_today = await get_total_stars_today()

    await message.answer(
        f"🛠 Админ-панель\n\n"
        f"👥 Пользователей: {total}\n"
        f"🟢 Онлайн: {online}\n"
        f"💬 Диалогов сегодня: {chats_today}\n"
        f"💰 Заработано сегодня: {stars_today}⭐",
        reply_markup=admin_kb(),
    )


@admin_router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    total = await get_total_users()
    new_today = await get_new_users_today()
    chats_today = await get_total_chats_today()
    stars_today = await get_total_stars_today()
    premium = await get_premium_count()

    await callback.message.edit_text(
        f"📊 Статистика бота\n\n"
        f"Сегодня:\n"
        f"👤 Новых: {new_today}\n"
        f"💬 Диалогов: {chats_today}\n"
        f"💰 Звёзд: {stars_today}⭐\n\n"
        f"Всего:\n"
        f"👥 Пользователей: {total}\n"
        f"⭐ Premium: {premium}",
        reply_markup=admin_kb(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:money")
async def cb_admin_money(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return

    stars_today = await get_total_stars_today()
    premium = await get_premium_count()

    await callback.message.edit_text(
        f"💰 Монетизация\n\n"
        f"Сегодня: {stars_today}⭐\n"
        f"💎 Активных Premium: {premium}",
        reply_markup=admin_kb(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:reports")
async def cb_admin_reports(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return

    reports = await get_pending_reports()
    if not reports:
        await callback.message.edit_text("🚩 Жалоб нет! 🎉", reply_markup=admin_kb())
        await callback.answer()
        return

    report = reports[0]
    await callback.message.edit_text(
        f"🚩 Жалоба #{report.id}\n\n"
        f"От: #{report.reporter_id}\n"
        f"На: #{report.reported_id}\n"
        f"Причина: {report.reason}\n"
        f"Дата: {report.created_at.strftime('%d.%m %H:%M')}",
        reply_markup=admin_report_kb(report.id),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin:ban1:"))
async def cb_ban_1day(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    report_id = int(callback.data.split(":")[-1])
    reports = await get_pending_reports()
    report = next((r for r in reports if r.id == report_id), None)
    if report:
        await ban_user(report.reported_id, report.reason, 24, callback.from_user.id, report.id)
        await resolve_report(report.id, callback.from_user.id)
    await callback.answer("⛔ Пользователь забанен на 1 день", show_alert=True)
    await cb_admin_reports(callback)


@admin_router.callback_query(F.data.startswith("admin:banf:"))
async def cb_ban_forever(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    report_id = int(callback.data.split(":")[-1])
    reports = await get_pending_reports()
    report = next((r for r in reports if r.id == report_id), None)
    if report:
        await ban_user(report.reported_id, report.reason, None, callback.from_user.id, report.id)
        await resolve_report(report.id, callback.from_user.id)
    await callback.answer("⛔ Пользователь забанен навсегда", show_alert=True)
    await cb_admin_reports(callback)


@admin_router.callback_query(F.data.startswith("admin:dismiss:"))
async def cb_dismiss(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    report_id = int(callback.data.split(":")[-1])
    await resolve_report(report_id, callback.from_user.id, "dismissed")
    await callback.answer("✅ Жалоба отклонена")
    await cb_admin_reports(callback)


@admin_router.callback_query(F.data == "admin:bans")
async def cb_admin_bans(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 Управление банами\n\nИспользуй /ban_user <anon_id> для бана вручную.",
        reply_markup=admin_kb(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.edit_text(
        "👥 Поиск пользователя\nВведи /user <anon_id>",
        reply_markup=admin_kb(),
    )
    await callback.answer()


# ── Broadcast ────────────────────────────────────────────────────

_broadcast_state: dict[int, str] = {}


@admin_router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.edit_text(
        "📢 Массовая рассылка\n\nВыбери аудиторию:",
        reply_markup=admin_broadcast_audience_kb(),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("bc:"))
async def cb_broadcast_audience(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    audience = callback.data.split(":")[-1]
    _broadcast_state[callback.from_user.id] = audience

    audience_text = {
        "all": "всем пользователям",
        "premium": "только Premium",
        "inactive": "неактивным 7+ дней",
        "nopay": "без покупок",
    }.get(audience, "всем")  # BUG FIX: was "auditorium"

    await callback.message.edit_text(
        f"📢 Рассылка {audience_text}\n\n"
        f"Введи текст сообщения:"
    )
    await callback.answer()


@admin_router.message(F.command("broadcast"))
async def cmd_broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text[len("/broadcast "):]
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return

    await _do_broadcast(message, text)


async def _do_broadcast(message: Message, text: str):
    from sqlalchemy import select
    from models.base import async_session
    from models.user import User
    from datetime import datetime, timedelta

    user_id = message.from_user.id
    audience = _broadcast_state.get(user_id, "all")

    async with async_session() as session:
        query = select(User).where(User.is_banned == False)
        if audience == "premium":
            query = query.where(User.is_premium == True)
        elif audience == "inactive":
            cutoff = datetime.utcnow() - timedelta(days=7)
            query = query.where(User.last_online < cutoff)
        elif audience == "nopay":
            query = query.where(User.total_stars_spent == 0)

        result = await session.execute(query.limit(5000))
        users = result.scalars().all()

    sent = 0
    failed = 0
    for user in users:
        try:
            await message.bot.send_message(user.telegram_id, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"📢 Рассылка завершена!\n\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
    )


@admin_router.callback_query(F.data == "admin:settings")
async def cb_admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    from config import CHAT_DELAY_NEXT, STARS_INSTANT_SEARCH, STARS_REVEAL_ONE, STARS_PREMIUM_1M
    await callback.message.edit_text(
        f"⚙️ Настройки бота\n\n"
        f"Задержка «Следующий»: {CHAT_DELAY_NEXT} сек\n"
        f"Мгновенный поиск: {STARS_INSTANT_SEARCH}⭐\n"
        f"Раскрытие: {STARS_REVEAL_ONE}⭐\n"
        f"Premium 1 мес: {STARS_PREMIUM_1M}⭐\n",
        reply_markup=admin_kb(),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:funnel")
async def cb_admin_funnel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    total = await get_total_users()
    premium = await get_premium_count()
    await callback.message.edit_text(
        f"📈 Воронка\n\n"
        f"Регистрация: {total}\n"
        f"Premium: {premium} ({round(premium/max(total,1)*100, 1)}%)\n",
        reply_markup=admin_kb(),
    )
    await callback.answer()


# ── Admin text commands ──────────────────────────────────────────

@admin_router.message(F.command("ban_user"))
async def cmd_ban_user(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /ban_user <anon_id> [часы]")
        return
    anon_id = parts[1]
    hours = int(parts[2]) if len(parts) > 2 else None
    user = await get_user_by_anon_id(anon_id)
    if not user:
        await message.answer(f"Пользователь {anon_id} не найден")
        return
    await ban_user(user.telegram_id, "Manual ban by admin", hours, message.from_user.id)
    await message.answer(f"⛔ {anon_id} забанен" + (f" на {hours}ч" if hours else " навсегда"))


@admin_router.message(F.command("user"))
async def cmd_user(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /user <anon_id>")
        return
    anon_id = parts[1]
    user = await get_user_by_anon_id(anon_id)
    if not user:
        await message.answer(f"Пользователь {anon_id} не найден")
        return

    premium_text = f"до {user.premium_expires.strftime('%d.%m.%Y')}" if user.is_premium and user.premium_expires else "нет"
    await message.answer(
        f"👤 {user.anon_id}\n"
        f"🆔 tg_id: {user.telegram_id}\n"
        f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y')}\n"
        f"💬 Диалогов: {user.chats_count}\n"
        f"⭐ Premium: {premium_text}\n"
        f"💰 Потратил: {user.total_stars_spent}⭐\n"
        f"🚫 Бан: {'да' if user.is_banned else 'нет'}"
    )
