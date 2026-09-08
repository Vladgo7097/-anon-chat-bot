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
            InlineKeyboardButton(text="Реклама", icon_custom_emoji_id="5215668805199473901", callback_data="admin:ads"),
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


def admin_broadcast_audience_kb(sizes=None) -> InlineKeyboardMarkup:
    sizes = sizes or {}

    def label(text, segment):
        size = sizes.get(segment)
        return text if size is None else f"{text} ({size:,})".replace(",", " ")

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label("Все", "all"), icon_custom_emoji_id="5942877472163892475", callback_data="bc:all")],
        [InlineKeyboardButton(text=label("Premium", "premium"), icon_custom_emoji_id="5258165702707125574", callback_data="bc:premium")],
        [InlineKeyboardButton(text=label("Неактивные 7+ дней", "inactive"), icon_custom_emoji_id="5949785428843302949", callback_data="bc:inactive")],
        [InlineKeyboardButton(text=label("Без покупок", "nopay"), icon_custom_emoji_id="5271783639548441015", callback_data="bc:nopay")],
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
        done = job.sent+job.failed
        # Drafts have no audience snapshot yet (total stays 0 until
        # confirm_broadcast()); showing a fake "0/1" implied a pending
        # recipient that doesn't exist.
        progress = "черновик" if job.status == "draft" else f"{done}/{job.total}"
        rows.append([InlineKeyboardButton(
            text=f"#{job.id} · {job.segment} · {status_label} · {progress}",
            icon_custom_emoji_id=emoji_id,
            callback_data=f"admin:broadcasts:{job.id}")])
    rows.append([InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ads_kb(campaigns) -> InlineKeyboardMarkup:
    status_label = {"draft": "Черновик", "active": "Идёт", "paused": "Пауза"}
    rows = [[InlineKeyboardButton(
        text=f"#{c.id} · {status_label.get(c.status, c.status)} · {c.title[:20]} · {c.impressions_total}",
        callback_data=f"admin:ad:{c.id}")] for c in campaigns]
    rows.append([InlineKeyboardButton(text="Новая кампания", icon_custom_emoji_id="5188481279963715781", callback_data="admin:ad:new", style="primary")])
    rows.append([InlineKeyboardButton(text="Настройки рекламы", icon_custom_emoji_id="6129805886383723340", callback_data="admin:ads:settings")])
    rows.append([InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ads_settings_kb(cooldown_seconds) -> InlineKeyboardMarkup:
    def mark(seconds):
        return " ✓" if cooldown_seconds == seconds else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"10 мин{mark(600)}", callback_data="admin:ads:settings:set:600"),
            InlineKeyboardButton(text=f"30 мин{mark(1800)}", callback_data="admin:ads:settings:set:1800"),
            InlineKeyboardButton(text=f"60 мин{mark(3600)}", callback_data="admin:ads:settings:set:3600"),
        ],
        [InlineKeyboardButton(text="Своё значение", icon_custom_emoji_id="5301173701323028420", callback_data="admin:ads:settings:custom")],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:ads")],
    ])


def admin_ads_settings_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отменить", icon_custom_emoji_id="5312229088876847138", callback_data="admin:ads:settings:cancel", style="danger")]])


def admin_ad_card_kb(campaign) -> InlineKeyboardMarkup:
    rows = []
    if campaign.status == "draft":
        rows.append([
            InlineKeyboardButton(text="Запустить", icon_custom_emoji_id="5188481279963715781", callback_data=f"admin:ad:launch:{campaign.id}", style="success"),
            InlineKeyboardButton(text="Удалить", icon_custom_emoji_id="5312229088876847138", callback_data=f"admin:ad:delete:{campaign.id}", style="danger"),
        ])
    elif campaign.status == "active":
        rows.append([InlineKeyboardButton(text="Пауза", callback_data=f"admin:ad:pause:{campaign.id}", style="danger")])
    elif campaign.status == "paused":
        rows.append([InlineKeyboardButton(text="Возобновить", callback_data=f"admin:ad:resume:{campaign.id}", style="success")])
    if campaign.status in {"active", "paused"}:
        rows.append([InlineKeyboardButton(text="Завершить", callback_data=f"admin:ad:complete:{campaign.id}", style="danger")])
    rows.append([InlineKeyboardButton(text="К списку", callback_data="admin:ads")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
