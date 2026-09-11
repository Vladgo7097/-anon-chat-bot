from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from keyboards import texts as T
from locales import LANGS

SEGMENT_TITLES = {"all": "все пользователи", "premium": "Premium",
                  "inactive": "неактивные 7+ дней", "nopay": "без покупок"}


def language_kb(back_callback=None):
    rows = [[InlineKeyboardButton(
        text=f"{T.LANGUAGE_FLAGS[code]} {T.LANGUAGE_NAMES[code]}",
        callback_data=f"lang:{code}")] for code in LANGS]
    if back_callback:
        rows.append([InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    """Plain 'Назад' row for admin screens that dead-ended without one
    (bans list, bot settings, user card) -- same admin:back target every
    other admin screen's back button already uses."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")]])


def profile_kb(has_photo: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Заполнить о себе", icon_custom_emoji_id="5301173701323028420", callback_data="profile:edit")],
        [InlineKeyboardButton(text="Добавить фото", icon_custom_emoji_id="5258254475386167466", callback_data="profile:photo")],
    ]
    if has_photo:
        rows.append([InlineKeyboardButton(text="Удалить фото", icon_custom_emoji_id="5312229088876847138", callback_data="profile:photo:delete")])
    rows += [
        [InlineKeyboardButton(text="Приватность", icon_custom_emoji_id="5350491749926058609", callback_data="profile:privacy")],
        [InlineKeyboardButton(text="Приглашения, стрик и награды", icon_custom_emoji_id="5345843457145453967", callback_data="profile:rewards",style="primary")],
        [InlineKeyboardButton(text="Удалить аккаунт", icon_custom_emoji_id="5337131193294938034", callback_data="profile:delete")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        [InlineKeyboardButton(text="🌐 Язык / Language", callback_data="language:menu")],
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
            InlineKeyboardButton(text="📊 Опрос", callback_data="admin:poll"),
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


def admin_broadcast_audience_kb(sizes=None, poll=False) -> InlineKeyboardMarkup:
    sizes = sizes or {}

    def label(text, segment):
        size = sizes.get(segment)
        return text if size is None else f"{text} ({size:,})".replace(",", " ")

    # callback_data written as literal strings (not an f-string with a
    # runtime prefix) so tests/test_callback_routing.py's static source scan
    # can actually resolve and verify both variants -- it only understands
    # quoted literals, not values built from a variable at call time.
    if poll:
        rows = [
            [InlineKeyboardButton(text=label("Все", "all"), icon_custom_emoji_id="5942877472163892475", callback_data="bcp:all")],
            [InlineKeyboardButton(text=label("Premium", "premium"), icon_custom_emoji_id="5258165702707125574", callback_data="bcp:premium")],
            [InlineKeyboardButton(text=label("Неактивные 7+ дней", "inactive"), icon_custom_emoji_id="5949785428843302949", callback_data="bcp:inactive")],
            [InlineKeyboardButton(text=label("Без покупок", "nopay"), icon_custom_emoji_id="5271783639548441015", callback_data="bcp:nopay")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text=label("Все", "all"), icon_custom_emoji_id="5942877472163892475", callback_data="bc:all")],
            [InlineKeyboardButton(text=label("Premium", "premium"), icon_custom_emoji_id="5258165702707125574", callback_data="bc:premium")],
            [InlineKeyboardButton(text=label("Неактивные 7+ дней", "inactive"), icon_custom_emoji_id="5949785428843302949", callback_data="bc:inactive")],
            [InlineKeyboardButton(text=label("Без покупок", "nopay"), icon_custom_emoji_id="5271783639548441015", callback_data="bc:nopay")],
        ]
    rows.append([InlineKeyboardButton(text="Отмена", icon_custom_emoji_id="5210952531676504517", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm_ban_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 день", callback_data=f"admin:doban:{telegram_id}:24", style="danger"),
        InlineKeyboardButton(text="7 дней", callback_data=f"admin:doban:{telegram_id}:168", style="danger"),
        InlineKeyboardButton(text="Навсегда", callback_data=f"admin:doban:{telegram_id}:0", style="danger"),
    ], [
        InlineKeyboardButton(text="Отмена", icon_custom_emoji_id="5210952531676504517", callback_data=f"admin:ucard:{telegram_id}"),
    ]])


def admin_confirm_grant_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 месяц", callback_data=f"admin:dogrant:{telegram_id}:30", style="success"),
        InlineKeyboardButton(text="3 месяца", callback_data=f"admin:dogrant:{telegram_id}:90", style="success"),
        InlineKeyboardButton(text="12 месяцев", callback_data=f"admin:dogrant:{telegram_id}:365", style="success"),
    ], [
        InlineKeyboardButton(text="Отмена", icon_custom_emoji_id="5210952531676504517", callback_data=f"admin:ucard:{telegram_id}"),
    ]])


def admin_user_card_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Забанить", icon_custom_emoji_id="5260342697075416641", callback_data=f"admin:uban:{telegram_id}", style="danger"),
            InlineKeyboardButton(text="Выдать Premium", icon_custom_emoji_id="5920281855378068765", callback_data=f"admin:ugrant:{telegram_id}", style="success"),
        ],
        [
            InlineKeyboardButton(text="Написать от бота", icon_custom_emoji_id="5224455462178549802", callback_data=f"admin:message:{telegram_id}", style="primary"),
        ],
        [
            InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back"),
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
            text=f"#{job.id} · {SEGMENT_TITLES.get(job.segment, job.segment)} · {status_label} · {progress}",
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
