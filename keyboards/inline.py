from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
















def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5301173701323028420\">✏️</tg-emoji> Заполнить о себе", callback_data="profile:edit")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5258254475386167466\">🖼</tg-emoji> Добавить фото", callback_data="profile:photo")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5350491749926058609\">🔒</tg-emoji> Приватность", callback_data="profile:privacy")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5345843457145453967\">🎁</tg-emoji> Приглашения, стрик и награды", callback_data="profile:rewards",style="primary")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5337131193294938034\">🗑</tg-emoji> Удалить аккаунт", callback_data="profile:delete")],
    ])


def profile_edit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5366121041426922772\">👤</tg-emoji> Пол", callback_data="profile:set_gender")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Возраст", callback_data="profile:set_age")],
        [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile")],
    ])


def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5366121041426922772\">👨</tg-emoji> Мужчин", callback_data="settings:gender:m")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5366209534933089956\">👩</tg-emoji> Женщин", callback_data="settings:gender:f")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5253959125838090076\">🌈</tg-emoji> Всех", callback_data="settings:gender:a")],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5386637391530317781\">🔽</tg-emoji> 18-25", callback_data="settings:age:18-25"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5386637391530317781\">🔽</tg-emoji> 26-35", callback_data="settings:age:26-35"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5386637391530317781\">🔽</tg-emoji> 36+", callback_data="settings:age:36+"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5271934788037517525\">♾</tg-emoji> Любой", callback_data="settings:age:any"),
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
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5931472654660800739\">📊</tg-emoji> Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5348076097110050117\">💰</tg-emoji> Монетизация", callback_data="admin:money"),
        ],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> Жалобы", callback_data="admin:reports"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5258318620722733379\">🚫</tg-emoji> Баны", callback_data="admin:bans"),
        ],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5942877472163892475\">👥</tg-emoji> Пользователи", callback_data="admin:users"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5463212105752664600\">📢</tg-emoji> Рассылка", callback_data="admin:broadcast"),
        ],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5467526417581357662\">📋</tg-emoji> Рассылки (статус)", callback_data="admin:broadcasts"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5399909394525737759\">📈</tg-emoji> Воронка", callback_data="admin:funnel"),
        ],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Настройки бота", callback_data="admin:settings"),
        ],
    ])


def admin_report_kb(report_id: int, offset: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5260342697075416641\">⛔</tg-emoji> Забанить на 1 день", callback_data=f"admin:ban1:{report_id}"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5260342697075416641\">⛔</tg-emoji> Навсегда", callback_data=f"admin:banf:{report_id}"),
        ],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5210952531676504517\">✅</tg-emoji> Отклонить", callback_data=f"admin:dismiss:{report_id}"),
            InlineKeyboardButton(text="⏭ Следующая", callback_data=f"admin:reports:{offset}"),
        ],
    ])


def admin_broadcast_audience_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5942877472163892475\">👥</tg-emoji> Все", callback_data="bc:all")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5258165702707125574\">⭐</tg-emoji> Premium", callback_data="bc:premium")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5949785428843302949\">😴</tg-emoji> Неактивные 7+ дней", callback_data="bc:inactive")],
        [InlineKeyboardButton(text="<tg-emoji emoji-id=\"5271783639548441015\">🆓</tg-emoji> Без покупок", callback_data="bc:nopay")],
    ])


def admin_user_card_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5260342697075416641\">⛔</tg-emoji> Забанить", callback_data=f"admin:uban:{telegram_id}", style="danger"),
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5920281855378068765\">🎁</tg-emoji> Выдать Premium", callback_data=f"admin:ugrant:{telegram_id}", style="success"),
        ],
        [
            InlineKeyboardButton(text="<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Написать от бота", callback_data=f"admin:message:{telegram_id}", style="primary"),
        ],
    ])


def admin_broadcast_status_kb(jobs) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        status_icon = {"draft": "<tg-emoji emoji-id=\"5460865451586250451\">📝</tg-emoji>", "queued": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji>", "sending": "<tg-emoji emoji-id=\"5967432491684860012\">📤</tg-emoji>", "completed": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji>"}.get(job.status, "<tg-emoji emoji-id=\"5280880410346152584\">❓</tg-emoji>")
        rows.append([InlineKeyboardButton(
            text=f"{status_icon} #{job.id} · {job.segment} · {job.sent}/{job.sent+job.failed+job.blocked}",
            callback_data=f"admin:broadcast_detail:{job.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
