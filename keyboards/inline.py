from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Заполнить о себе", icon_custom_emoji_id="5301173701323028420", callback_data="profile:edit")],
        [InlineKeyboardButton(text="Добавить фото", icon_custom_emoji_id="5258254475386167466", callback_data="profile:photo")],
        [InlineKeyboardButton(text="Приватность", icon_custom_emoji_id="5350491749926058609", callback_data="profile:privacy")],
        [InlineKeyboardButton(text="Приглашения, стрик и награды", icon_custom_emoji_id="5345843457145453967", callback_data="profile:rewards",style="primary")],
        [InlineKeyboardButton(text="Удалить аккаунт", icon_custom_emoji_id="5337131193294938034", callback_data="profile:delete")],
    ])


def profile_edit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пол", icon_custom_emoji_id="5366121041426922772", callback_data="profile:set_gender")],
        [InlineKeyboardButton(text="Возраст", icon_custom_emoji_id="5452055425690123301", callback_data="profile:set_age")],
        [InlineKeyboardButton(text="Назад в профиль", icon_custom_emoji_id="5258236805890710909", callback_data="profile")],
    ])


def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мужчин", icon_custom_emoji_id="5366121041426922772", callback_data="settings:gender:m")],
        [InlineKeyboardButton(text="Женщин", icon_custom_emoji_id="5366209534933089956", callback_data="settings:gender:f")],
        [InlineKeyboardButton(text="Всех", icon_custom_emoji_id="5253959125838090076", callback_data="settings:gender:a")],
        [
            InlineKeyboardButton(text="18-25", icon_custom_emoji_id="5386637391530317781", callback_data="settings:age:18-25"),
            InlineKeyboardButton(text="26-35", icon_custom_emoji_id="5386637391530317781", callback_data="settings:age:26-35"),
            InlineKeyboardButton(text="36+", icon_custom_emoji_id="5386637391530317781", callback_data="settings:age:36+"),
            InlineKeyboardButton(text="Любой", icon_custom_emoji_id="5271934788037517525", callback_data="settings:age:any"),
        ],
    ])


def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="menu")],
    ])


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="menu")],
    ])


# ── Admin keyboards ──────────────────────────────────────────────

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Статистика", icon_custom_emoji_id="5931472654660800739", callback_data="admin:stats"),
            InlineKeyboardButton(text="Монетизация", icon_custom_emoji_id="5348076097110050117", callback_data="admin:money"),
        ],
        [
            InlineKeyboardButton(text="Жалобы", icon_custom_emoji_id="5280880410346152584", callback_data="admin:reports"),
            InlineKeyboardButton(text="Баны", icon_custom_emoji_id="5258318620722733379", callback_data="admin:bans"),
        ],
        [
            InlineKeyboardButton(text="Пользователи", icon_custom_emoji_id="5942877472163892475", callback_data="admin:users"),
            InlineKeyboardButton(text="Рассылка", icon_custom_emoji_id="5463212105752664600", callback_data="admin:broadcast"),
        ],
        [
            InlineKeyboardButton(text="Рассылки (статус)", icon_custom_emoji_id="5467526417581357662", callback_data="admin:broadcasts"),
            InlineKeyboardButton(text="Воронка", icon_custom_emoji_id="5399909394525737759", callback_data="admin:funnel"),
        ],
        [
            InlineKeyboardButton(text="Настройки бота", icon_custom_emoji_id="6129805886383723340", callback_data="admin:settings"),
        ],
    ])


def admin_report_kb(report_id: int, offset: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Забанить на 1 день", icon_custom_emoji_id="5260342697075416641", callback_data=f"admin:ban1:{report_id}", style="danger"),
            InlineKeyboardButton(text="Навсегда", icon_custom_emoji_id="5260342697075416641", callback_data=f"admin:banf:{report_id}", style="danger"),
        ],
        [
            InlineKeyboardButton(text="Отклонить", icon_custom_emoji_id="5210952531676504517", callback_data=f"admin:dismiss:{report_id}"),
            InlineKeyboardButton(text="Следующая", callback_data=f"admin:reports:{offset}"),
        ],
    ])


def admin_broadcast_audience_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Все", icon_custom_emoji_id="5942877472163892475", callback_data="bc:all")],
        [InlineKeyboardButton(text="Premium", icon_custom_emoji_id="5258165702707125574", callback_data="bc:premium")],
        [InlineKeyboardButton(text="Неактивные 7+ дней", icon_custom_emoji_id="5949785428843302949", callback_data="bc:inactive")],
        [InlineKeyboardButton(text="Без покупок", icon_custom_emoji_id="5271783639548441015", callback_data="bc:nopay")],
    ])


def admin_user_card_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Забанить", icon_custom_emoji_id="5260342697075416641", callback_data=f"admin:uban:{telegram_id}", style="danger"),
            InlineKeyboardButton(text="Выдать Premium", icon_custom_emoji_id="5920281855378068765", callback_data=f"admin:ugrant:{telegram_id}", style="success"),
        ],
        [
            InlineKeyboardButton(text="Написать от бота", icon_custom_emoji_id="5224455462178549802", callback_data=f"admin:message:{telegram_id}", style="primary"),
        ],
    ])


def admin_broadcast_status_kb(jobs) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        status_info = {"draft": ("5460865451586250451", "Черновик"), "queued": ("5296482716567495148", "Ожидает"), "sending": ("5967432491684860012", "Отправка"), "completed": ("5280880410346152584", "Готово")}.get(job.status, ("5280880410346152584", "?"))
        emoji_id, status_label = status_info
        rows.append([InlineKeyboardButton(
            text=f"#{job.id} · {job.segment} · {job.sent}/{job.sent+job.failed+job.blocked}",
            icon_custom_emoji_id=emoji_id,
            callback_data=f"admin:broadcasts:{job.id}")])
    rows.append([InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
