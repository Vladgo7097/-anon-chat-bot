from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from keyboards import texts as T
from locales import LANGS

SEGMENT_TITLES = {"all": "все пользователи", "premium": "Premium",
                  "inactive": "неактивные 7+ дней", "nopay": "без покупок",
                  "no_language": "не дошли регистрации"}


def language_kb(back_callback=None, lang="ru"):
    rows = [[InlineKeyboardButton(
        text=f"{T.LANGUAGE_FLAGS[code]} {T.LANGUAGE_NAMES[code]}",
        callback_data=f"lang:{code}")] for code in LANGS]
    if back_callback:
        rows.append([InlineKeyboardButton(text=T.label(T.PAGE_PREV, lang), icon_custom_emoji_id="5258236805890710909", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_kb(target="admin:back", label="Назад") -> InlineKeyboardMarkup:
    """One back row. target is the section callback the screen belongs to;
    admin:back is the root, which every legacy screen already used."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, icon_custom_emoji_id="5258236805890710909",
                             callback_data=target)]])


def back_kb(target="admin:back") -> InlineKeyboardMarkup:
    """Plain 'Назад' row for admin screens that dead-ended without one
    (bans list, bot settings, user card) -- same admin:back target every
    other admin screen's back button already uses, unless a section
    callback is passed to keep the navigation one level deep."""
    return admin_back_kb(target)


def profile_kb(has_photo: bool = False, lang: str = "ru") -> InlineKeyboardMarkup:
    labels = T.tr(T.PROFILE_BUTTONS, lang)
    rows = [
        [InlineKeyboardButton(text=labels["fill"], icon_custom_emoji_id="5301173701323028420", callback_data="profile:edit")],
        [InlineKeyboardButton(text=labels["photo"], icon_custom_emoji_id="5258254475386167466", callback_data="profile:photo")],
    ]
    if has_photo:
        rows.append([InlineKeyboardButton(text=labels["photo_delete"], icon_custom_emoji_id="5312229088876847138", callback_data="profile:photo:delete")])
    rows += [
        [InlineKeyboardButton(text=labels["privacy"], icon_custom_emoji_id="5350491749926058609", callback_data="profile:privacy")],
        [InlineKeyboardButton(text=labels["rewards"], icon_custom_emoji_id="5345843457145453967", callback_data="profile:rewards",style="primary")],
        [InlineKeyboardButton(text="🌐 Язык / Language", callback_data="language:menu:profile")],
        [InlineKeyboardButton(text=labels["delete"], icon_custom_emoji_id="5337131193294938034", callback_data="profile:delete")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_edit_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    labels = T.tr(T.PROFILE_EDIT_BUTTONS, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels["gender"], icon_custom_emoji_id="5366121041426922772", callback_data="profile:set_gender")],
        [InlineKeyboardButton(text=labels["age"], icon_custom_emoji_id="5452055425690123301", callback_data="profile:set_age")],
        [InlineKeyboardButton(text=labels["bio"], icon_custom_emoji_id="5301173701323028420", callback_data="profile:set_bio")],
        [InlineKeyboardButton(text=labels["back"], icon_custom_emoji_id="5258236805890710909", callback_data="profile")],
    ])


def settings_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    labels = T.tr(T.SETTINGS_BUTTONS, lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels["m"], icon_custom_emoji_id="5366121041426922772", callback_data="settings:gender:m")],
        [InlineKeyboardButton(text=labels["f"], icon_custom_emoji_id="5366209534933089956", callback_data="settings:gender:f")],
        [InlineKeyboardButton(text=labels["a"], icon_custom_emoji_id="5253959125838090076", callback_data="settings:gender:a")],
        [
            InlineKeyboardButton(text="18-25", icon_custom_emoji_id="5386637391530317781", callback_data="settings:age:18-25"),
            InlineKeyboardButton(text="26-35", icon_custom_emoji_id="5386637391530317781", callback_data="settings:age:26-35"),
            InlineKeyboardButton(text="36+", icon_custom_emoji_id="5386637391530317781", callback_data="settings:age:36+"),
            InlineKeyboardButton(text=labels["any"], icon_custom_emoji_id="5271934788037517525", callback_data="settings:age:any"),
        ],
        [InlineKeyboardButton(text="🌐 Язык / Language", callback_data="language:menu")],
    ])


def stats_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=T.label(T.PAGE_PREV, lang), icon_custom_emoji_id="5258236805890710909", callback_data="menu")],
    ])


def help_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=T.label(T.PAGE_PREV, lang), icon_custom_emoji_id="5258236805890710909", callback_data="menu")],
    ])


# ── Admin keyboards ──────────────────────────────────────────────

def admin_kb() -> InlineKeyboardMarkup:
    """Panel root: seven sections, two per row.

    One screen per section, every screen back to its section, every section
    back to this root -- the whole panel is two taps from anywhere."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Аналитика", callback_data="admin:sec:analytics"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:sec:users"),
        ],
        [
            InlineKeyboardButton(text="🛡 Модерация", callback_data="admin:sec:moderation"),
            InlineKeyboardButton(text="📨 Рассылки", callback_data="admin:sec:broadcast"),
        ],
        [
            InlineKeyboardButton(text="📢 Реклама", callback_data="admin:sec:ads"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:sec:settings"),
        ],
        [
            InlineKeyboardButton(text="🧩 Обязательная подписка", callback_data="admin:op"),
            InlineKeyboardButton(text="🧰 Система", callback_data="admin:sec:system"),
        ],
    ])


def admin_analytics_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Статистика", icon_custom_emoji_id="5931472654660800739", callback_data="admin:stats"),
            InlineKeyboardButton(text="Монетизация", icon_custom_emoji_id="5348076097110050117", callback_data="admin:money"),
        ],
        [
            InlineKeyboardButton(text="Воронка", icon_custom_emoji_id="5399909394525737759", callback_data="admin:funnel"),
            InlineKeyboardButton(text="Рост", callback_data="admin:growth:1"),
        ],
        [
            InlineKeyboardButton(text="События", callback_data="admin:events:7"),
            InlineKeyboardButton(text="Выгрузка CSV", callback_data="admin:export:1"),
        ],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")],
    ])


def admin_users_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin:users")],
        [
            InlineKeyboardButton(text="🏆 Топ по тратам", callback_data="admin:top"),
            InlineKeyboardButton(text="🆕 Новые", callback_data="admin:recent"),
        ],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")],
    ])


def admin_moderation_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Жалобы", icon_custom_emoji_id="5280880410346152584", callback_data="admin:reports"),
            InlineKeyboardButton(text="Баны", icon_custom_emoji_id="5258318620722733379", callback_data="admin:bans"),
        ],
        [InlineKeyboardButton(text="🎉 Сезонное событие", callback_data="admin:season")],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")],
    ])


def admin_broadcast_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Новая рассылка", icon_custom_emoji_id="5463212105752664600", callback_data="admin:broadcast"),
            InlineKeyboardButton(text="📊 Опрос", callback_data="admin:poll"),
        ],
        [InlineKeyboardButton(text="Статусы рассылок", icon_custom_emoji_id="5467526417581357662", callback_data="admin:broadcasts")],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")],
    ])


def admin_settings_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Цены на продукты", callback_data="admin:prices"),
            InlineKeyboardButton(text="Параметры бота", icon_custom_emoji_id="6129805886383723340", callback_data="admin:setlist:1"),
        ],
        [InlineKeyboardButton(text="Настройки рекламы", icon_custom_emoji_id="6129805886383723340", callback_data="admin:ads:settings")],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")],
    ])


def admin_system_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Состояние системы", callback_data="admin:syshealth")],
        [InlineKeyboardButton(text="Журнал действий", callback_data="admin:audit:0")],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:back")],
    ])


def admin_growth_kb(days: int = 1) -> InlineKeyboardMarkup:
    def mark(d):
        return " ✓" if days == d else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"24 ч{mark(1)}", callback_data="admin:growth:1"),
            InlineKeyboardButton(text=f"7 дней{mark(7)}", callback_data="admin:growth:7"),
            InlineKeyboardButton(text=f"30 дней{mark(30)}", callback_data="admin:growth:30"),
        ],
        [InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:sec:analytics")],
    ])


def admin_settings_kb(entries, page: int, pages: int) -> InlineKeyboardMarkup:
    """entries: (key, title, value) for the keys on this page."""
    rows = [[InlineKeyboardButton(text=f"{title}: {value}",
                                  callback_data=f"admin:setv:{key}")]
            for key, title, value in entries]
    if pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="‹ Назад", callback_data=f"admin:setlist:{page - 1}"))
        if page < pages:
            nav.append(InlineKeyboardButton(text="Далее ›", callback_data=f"admin:setlist:{page + 1}"))
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton(text="К разделу настроек",
                                      callback_data="admin:sec:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_setting_kb(key: str, page: int, presets, current) -> InlineKeyboardMarkup:
    """Presets for one setting, marked with the live value."""
    rows, row = [], []
    for value in presets:
        mark = "✓ " if str(value) == str(current) else ""
        row.append(InlineKeyboardButton(text=f"{mark}{value}",
                                        callback_data=f"admin:set:{key}:{value}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Своё значение", callback_data=f"admin:setc:{key}")])
    rows.append([InlineKeyboardButton(text="К списку параметров",
                                      callback_data=f"admin:setlist:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_prices_kb(products) -> InlineKeyboardMarkup:
    """products: rows with code, title, price_stars."""
    rows = [[InlineKeyboardButton(text=f"{p.title} — {p.price_stars} ⭐",
                                  callback_data=f"admin:price:{p.code}")]
            for p in products]
    rows.append([InlineKeyboardButton(text="К разделу настроек",
                                      callback_data="admin:sec:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_audit_kb(offset: int, step: int, has_more: bool) -> InlineKeyboardMarkup:
    rows = []
    if offset > 0:
        rows.append([InlineKeyboardButton(text="‹ Назад",
                                          callback_data=f"admin:audit:{max(0, offset - step)}")])
    if has_more:
        rows.append([InlineKeyboardButton(text="Далее ›",
                                          callback_data=f"admin:audit:{offset + step}")])
    rows.append([InlineKeyboardButton(text="К разделу «Система»",
                                      callback_data="admin:sec:system")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_list_kb(entries, target: str) -> InlineKeyboardMarkup:
    """entries: (telegram_id, label). target is the section callback to return to."""
    rows = [[InlineKeyboardButton(text=label, callback_data=f"admin:ucard:{telegram_id}")]
            for telegram_id, label in entries]
    rows.append([InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909",
                                      callback_data=target)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_report_kb(report_id: int, offset: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Забанить на 1 день", icon_custom_emoji_id="5260342697075416641", callback_data=f"admin:ban1:{report_id}", style="danger"),
            InlineKeyboardButton(text="Навсегда", icon_custom_emoji_id="5260342697075416641", callback_data=f"admin:banf:{report_id}", style="danger"),
        ],
        [
            InlineKeyboardButton(text="Мут 1 ч", callback_data=f"admin:mute1:{report_id}", style="primary"),
            InlineKeyboardButton(text="Мут 24 ч", callback_data=f"admin:mute24:{report_id}", style="primary"),
            InlineKeyboardButton(text="Мут 7 д", callback_data=f"admin:mute7:{report_id}", style="primary"),
        ],
        [
            InlineKeyboardButton(text="Отклонить", icon_custom_emoji_id="5210952531676504517", callback_data=f"admin:dismiss:{report_id}"),
            InlineKeyboardButton(text="Следующая", callback_data=f"admin:reports:{offset}"),
        ],
        [
            InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:sec:moderation"),
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
            [InlineKeyboardButton(text=label("Не дошли регистрации", "no_language"), icon_custom_emoji_id="5271783639548441015", callback_data="bcp:no_language")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text=label("Все", "all"), icon_custom_emoji_id="5942877472163892475", callback_data="bc:all")],
            [InlineKeyboardButton(text=label("Premium", "premium"), icon_custom_emoji_id="5258165702707125574", callback_data="bc:premium")],
            [InlineKeyboardButton(text=label("Неактивные 7+ дней", "inactive"), icon_custom_emoji_id="5949785428843302949", callback_data="bc:inactive")],
            [InlineKeyboardButton(text=label("Без покупок", "nopay"), icon_custom_emoji_id="5271783639548441015", callback_data="bc:nopay")],
            [InlineKeyboardButton(text=label("Не дошли регистрации", "no_language"), icon_custom_emoji_id="5271783639548441015", callback_data="bc:no_language")],
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


def admin_confirm_mute_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 час", callback_data=f"admin:domute:{telegram_id}:1", style="primary"),
        InlineKeyboardButton(text="24 часа", callback_data=f"admin:domute:{telegram_id}:24", style="primary"),
        InlineKeyboardButton(text="7 дней", callback_data=f"admin:domute:{telegram_id}:168", style="primary"),
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
            InlineKeyboardButton(text="Замутить", callback_data=f"admin:umute:{telegram_id}", style="primary"),
            InlineKeyboardButton(text="Снять мут", callback_data=f"admin:unmute:{telegram_id}"),
        ],
        [
            InlineKeyboardButton(text="Написать от бота", icon_custom_emoji_id="5224455462178549802", callback_data=f"admin:message:{telegram_id}", style="primary"),
        ],
        [
            InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:sec:users"),
        ],
    ])


def admin_broadcast_status_kb(jobs) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        status_info = {"draft": ("5460865451586250451", "Черновик"), "queued": ("5296482716567495148", "Ожидает"), "sending": ("5967432491684860012", "Отправка"), "completed": ("5280880410346152584", "Готово"), "cancelled": ("5312229088876847138", "Остановлена")}.get(job.status, ("5280880410346152584", "?"))
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
    rows.append([InlineKeyboardButton(text="Назад", icon_custom_emoji_id="5258236805890710909", callback_data="admin:sec:broadcast")])
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


# ── ОП v2: панель ───────────────────────────────────────────────

def _op_mark(target) -> str:
    if not target.is_active:
        return "⏸"
    return "✅" if target.setup_status == "verified" else ("⛔" if target.setup_status == "error" else "⚪")


def op_admin_kb(enabled: bool, paused: bool) -> InlineKeyboardMarkup:
    rows = []
    if enabled:
        rows.append([
            InlineKeyboardButton(
                text="▶️ Снять паузу" if paused else "⏸ Пауза",
                callback_data="admin:op:pause",
            ),
            InlineKeyboardButton(text="🔴 Выключить", callback_data="admin:op:off"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="🟢 Включить", callback_data="admin:op:on")])
    rows += [
        [
            InlineKeyboardButton(text="🗂 Ресурсы", callback_data="admin:op:res"),
            InlineKeyboardButton(text="✉️ Сообщения", callback_data="admin:op:msg"),
        ],
        [InlineKeyboardButton(text="🩺 Диагностика", callback_data="admin:op:diag")],
        [InlineKeyboardButton(text="← Админ-панель", callback_data="admin:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def op_resources_kb(targets) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"{_op_mark(target)} {target.title or target.chat_id}"[:60],
            callback_data=f"admin:op:r:{target.id}",
        )]
        for target in targets
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить ресурс", callback_data="admin:op:radd")])
    rows.append([InlineKeyboardButton(text="← К ОП", callback_data="admin:op")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def op_target_kb(target) -> InlineKeyboardMarkup:
    toggle = "⏸ Выключить" if target.is_active else "▶️ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Проверить права", callback_data=f"admin:op:rc:{target.id}"),
            InlineKeyboardButton(text=toggle, callback_data=f"admin:op:rt:{target.id}"),
        ],
        [InlineKeyboardButton(text="🔗 Ссылка для вступления", callback_data=f"admin:op:rl:{target.id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:op:rd:{target.id}")],
        [InlineKeyboardButton(text="← К ресурсам", callback_data="admin:op:res")],
    ])


def op_confirm_delete_kb(target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:op:rd2:{target_id}", style="danger")],
        [InlineKeyboardButton(text="← Отмена", callback_data=f"admin:op:r:{target_id}")],
    ])


def op_cancel_kb(target: str = "admin:op:res") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Отмена", callback_data=target)],
    ])


def op_single_kb(target: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=target)],
    ])


def op_diag_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Проверить все ресурсы", callback_data="admin:op:diag:check")],
        [InlineKeyboardButton(text="🧾 Журнал действий", callback_data="admin:op:audit")],
        [InlineKeyboardButton(text="← К ОП", callback_data="admin:op")],
    ])


def op_audit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Диагностика", callback_data="admin:op:diag")],
    ])


def op_messages_kb(entries) -> InlineKeyboardMarkup:
    """entries: (token, label) — токен кодирует global/error/feature."""
    rows = [
        [InlineKeyboardButton(text=label[:60], callback_data=f"admin:op:msg:{token}")]
        for token, label in entries
    ]
    rows.append([InlineKeyboardButton(text="➕ Шаблон для функции", callback_data="admin:op:msg:add")])
    rows.append([InlineKeyboardButton(text="🔘 Подписи кнопок", callback_data="admin:op:btn")])
    rows.append([InlineKeyboardButton(text="← К ОП", callback_data="admin:op")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def op_message_editor_kb(token: str, has_template: bool, fmt: str = "html") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"admin:op:msge:{token}")]]
    middle = [InlineKeyboardButton(text="👁 Предпросмотр", callback_data=f"admin:op:msgp:{token}")]
    if has_template:
        middle.insert(0, InlineKeyboardButton(
            text=f"🔤 Формат: {fmt}", callback_data=f"admin:op:msgf:{token}"))
    rows.append(middle)
    if has_template:
        rows.append([InlineKeyboardButton(text="♻️ Сбросить к встроенному", callback_data=f"admin:op:msgd:{token}")])
    rows.append([InlineKeyboardButton(text="← К сообщениям", callback_data="admin:op:msg")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def op_buttons_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Вступление", callback_data="admin:op:btn:join")],
        [InlineKeyboardButton(text="✏️ Проверка", callback_data="admin:op:btn:check")],
        [InlineKeyboardButton(text="✏️ Назад", callback_data="admin:op:btn:back")],
        [InlineKeyboardButton(text="← К сообщениям", callback_data="admin:op:msg")],
    ])


def op_confirm_reset_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♻️ Сбросить", callback_data=f"admin:op:msgd2:{token}", style="danger")],
        [InlineKeyboardButton(text="← Отмена", callback_data=f"admin:op:msg:{token}")],
    ])
