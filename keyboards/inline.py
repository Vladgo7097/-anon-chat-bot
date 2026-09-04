from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Начать поиск", callback_data="search:start")],
        [InlineKeyboardButton(text="ℹ️ Правила", callback_data="rules")],
    ])


def rules_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Понятно, продолжить", callback_data="rules:ok")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:start")],
    ])


def search_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить поиск", callback_data="search:cancel")],
        [InlineKeyboardButton(text="⚡ Найти мгновенно за 15⭐", callback_data="search:instant")],
    ])


def chat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏭ Следующий", callback_data="chat:next"),
            InlineKeyboardButton(text="🛑 Стоп", callback_data="chat:stop"),
        ],
        [
            InlineKeyboardButton(text="🚩 Пожаловаться", callback_data="chat:report"),
            InlineKeyboardButton(text="❤️ Раскрыть себя", callback_data="chat:reveal"),
        ],
    ])


def report_reasons_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔞 Контент 18+", callback_data="report:18plus")],
        [InlineKeyboardButton(text="🤬 Оскорбления", callback_data="report:insult")],
        [InlineKeyboardButton(text="📢 Спам/реклама", callback_data="report:spam")],
        [InlineKeyboardButton(text="👶 Несовершеннолетний", callback_data="report:minor")],
        [InlineKeyboardButton(text="❓ Другое", callback_data="report:other")],
    ])


def stop_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👍 Понравилось", callback_data="rate:plus"),
            InlineKeyboardButton(text="👎 Не понравилось", callback_data="rate:minus"),
        ],
        [
            InlineKeyboardButton(text="🔍 Новый поиск", callback_data="search:start"),
            InlineKeyboardButton(text="🏠 В меню", callback_data="menu"),
        ],
    ])


def reveal_offer_kb(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Раскрыться взаимно", callback_data="reveal:mutual")],
        [InlineKeyboardButton(text=f"👤 Узнать за 25⭐", callback_data="reveal:pay")],
        [InlineKeyboardButton(text="❌ Не сейчас", callback_data="chat:continue")],
    ])


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Заполнить о себе", callback_data="profile:edit")],
        [InlineKeyboardButton(text="🖼 Добавить фото", callback_data="profile:photo")],
        [InlineKeyboardButton(text="🔒 Приватность", callback_data="profile:privacy")],
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


def premium_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц — 99⭐", callback_data="premium:buy:1m")],
        [InlineKeyboardButton(text="3 месяца — 249⭐ (-16%)", callback_data="premium:buy:3m")],
        [InlineKeyboardButton(text="12 месяцев — 799⭐ (-33%)", callback_data="premium:buy:12m")],
    ])


def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти собеседника", callback_data="search:start")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton(text="⚙️ Настройки поиска", callback_data="settings")],
        [InlineKeyboardButton(text="⭐ Premium", callback_data="premium")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
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
            InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="admin:settings"),
            InlineKeyboardButton(text="📈 Воронка", callback_data="admin:funnel"),
        ],
    ])


def admin_report_kb(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⛔ Забанить на 1 день", callback_data=f"admin:ban1:{report_id}"),
            InlineKeyboardButton(text="⛔ Навсегда", callback_data=f"admin:banf:{report_id}"),
        ],
        [
            InlineKeyboardButton(text="✅ Отклонить", callback_data=f"admin:dismiss:{report_id}"),
            InlineKeyboardButton(text="⏭ Следующая", callback_data="admin:reports"),
        ],
    ])


def admin_broadcast_audience_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все", callback_data="bc:all")],
        [InlineKeyboardButton(text="⭐ Premium", callback_data="bc:premium")],
        [InlineKeyboardButton(text="😴 Неактивные 7+ дней", callback_data="bc:inactive")],
        [InlineKeyboardButton(text="🆓 Без покупок", callback_data="bc:nopay")],
    ])
