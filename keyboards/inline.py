from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
















def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Заполнить о себе", callback_data="profile:edit")],
        [InlineKeyboardButton(text="🖼 Добавить фото", callback_data="profile:photo")],
        [InlineKeyboardButton(text="🔒 Приватность", callback_data="profile:privacy")],
        [InlineKeyboardButton(text="🎁 Приглашения, стрик и награды", callback_data="profile:rewards",style="primary")],
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data="profile:delete")],
    ])


def profile_edit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Пол", callback_data="profile:set_gender")],
        [InlineKeyboardButton(text="🎂 Возраст", callback_data="profile:set_age")],
        [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile")],
    ])


def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужчин", callback_data="settings:gender:m")],
        [InlineKeyboardButton(text="👩 Женщин", callback_data="settings:gender:f")],
        [InlineKeyboardButton(text="🌈 Всех", callback_data="settings:gender:a")],
        [
            InlineKeyboardButton(text="🔽 18-25", callback_data="settings:age:18-25"),
            InlineKeyboardButton(text="🔽 26-35", callback_data="settings:age:26-35"),
            InlineKeyboardButton(text="🔽 36+", callback_data="settings:age:36+"),
            InlineKeyboardButton(text="♾ Любой", callback_data="settings:age:any"),
        ],
    ])






def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")],
    ])


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")],
    ])


# ── Admin keyboards ──────────────────────────────────────────────

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="💰 Монетизация", callback_data="admin:money"),
        ],
        [
            InlineKeyboardButton(text="🚩 Жалобы", callback_data="admin:reports"),
            InlineKeyboardButton(text="🚫 Баны", callback_data="admin:bans"),
        ],
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
            InlineKeyboardButton(text="📢 Рассылка", callback_data="admin:broadcast"),
        ],
        [
            InlineKeyboardButton(text="📋 Рассылки (статус)", callback_data="admin:broadcasts"),
            InlineKeyboardButton(text="📈 Воронка", callback_data="admin:funnel"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="admin:settings"),
        ],
    ])


def admin_report_kb(report_id: int, offset: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⛔ Забанить на 1 день", callback_data=f"admin:ban1:{report_id}"),
            InlineKeyboardButton(text="⛔ Навсегда", callback_data=f"admin:banf:{report_id}"),
        ],
        [
            InlineKeyboardButton(text="✅ Отклонить", callback_data=f"admin:dismiss:{report_id}"),
            InlineKeyboardButton(text="⏭ Следующая", callback_data=f"admin:reports:{offset}"),
        ],
    ])


def admin_broadcast_audience_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все", callback_data="bc:all")],
        [InlineKeyboardButton(text="⭐ Premium", callback_data="bc:premium")],
        [InlineKeyboardButton(text="😴 Неактивные 7+ дней", callback_data="bc:inactive")],
        [InlineKeyboardButton(text="🆓 Без покупок", callback_data="bc:nopay")],
    ])


def admin_user_card_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⛔ Забанить", callback_data=f"admin:uban:{telegram_id}", style="danger"),
            InlineKeyboardButton(text="🎁 Выдать Premium", callback_data=f"admin:ugrant:{telegram_id}", style="success"),
        ],
        [
            InlineKeyboardButton(text="💬 Написать от бота", callback_data=f"admin:message:{telegram_id}", style="primary"),
        ],
    ])


def admin_broadcast_status_kb(jobs) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        status_icon = {"draft": "📝", "queued": "⏳", "sending": "📤", "completed": "✅"}.get(job.status, "❓")
        rows.append([InlineKeyboardButton(
            text=f"{status_icon} #{job.id} · {job.segment} · {job.sent}/{job.sent+job.failed+job.blocked}",
            callback_data=f"admin:broadcast_detail:{job.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
