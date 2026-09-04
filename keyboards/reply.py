from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти собеседника"), KeyboardButton(text="👤 Мой профиль")],
            [KeyboardButton(text="⚙️ Настройки поиска"), KeyboardButton(text="⭐ Premium")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
    )


def chat_kb_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏭ Следующий"), KeyboardButton(text="🛑 Стоп")],
            [KeyboardButton(text="🚩 Пожаловаться"), KeyboardButton(text="❤️ Раскрыть себя")],
        ],
        resize_keyboard=True,
    )


def search_kb_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить поиск")],
        ],
        resize_keyboard=True,
    )
