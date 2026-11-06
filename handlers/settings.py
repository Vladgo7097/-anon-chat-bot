from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from handlers import settings_router
from keyboards import texts as T
from utils.rich_messages import card_blocks, details_block, rich_buttons, send_rich
from repositories.users import UserRepository
from services.settings import SettingsService

SETTINGS_TEXT = {
    "ru": '<tg-emoji emoji-id="6129805886383723340">⚙️</tg-emoji> Кого искать\n\nФильтр: {gender}\nВозраст: {min}–{max}\n\nФильтр по полу требует Premium или отдельной покупки; возрастной диапазон — Premium. После истечения доступа настройки сохраняются, но не применяются.',
    "en": '<tg-emoji emoji-id="6129805886383723340">⚙️</tg-emoji> Who to search for\n\nFilter: {gender}\nAge: {min}–{max}\n\nThe gender filter needs Premium or a separate purchase; the age range needs Premium. Settings stay saved after access expires, but stop applying.',
    "uk": '<tg-emoji emoji-id="6129805886383723340">⚙️</tg-emoji> Кого шукати\n\nФільтр: {gender}\nВік: {min}–{max}\n\nФільтр за статтю потребує Premium або окремої покупки; віковий діапазон — Premium. Після завершення доступу налаштування зберігаються, але не застосовуються.',
}
SAVED = {"ru": "Сохранено", "en": "Saved", "uk": "Збережено"}
INVALID_RANGE = {
    "ru": "Недопустимый диапазон.",
    "en": "Invalid range.",
    "uk": "Недопустимий діапазон.",
}
NEED_ACCESS = {
    "ru": "Нужен доступ к фильтру.",
    "en": "This filter needs access.",
    "uk": "Потрібен доступ до фільтра.",
}
PICK_ACCESS = {
    "ru": "Выбери доступ к фильтрам:",
    "en": "Choose access to filters:",
    "uk": "Обери доступ до фільтрів:",
}
ERRORS = {
    "ACTIVE_CHAT": {
        "ru": "Настройки нельзя менять во время диалога. Сначала заверши его.",
        "en": "Settings can't be changed during a chat. Finish it first.",
        "uk": "Налаштування не можна змінювати під час діалогу. Спочатку заверши його.",
    },
    "NOT_FOUND": {
        "ru": "Профиль не найден. Попробуй /start.",
        "en": "Profile not found. Try /start.",
        "uk": "Профіль не знайдено. Спробуй /start.",
    },
}
GENERIC_FAIL = {
    "ru": "Не удалось сохранить.",
    "en": "Could not save.",
    "uk": "Не вдалося зберегти.",
}
LANGUAGE_MENU_TEXT = {
    "ru": "🌐 Выбери язык интерфейса:",
    "en": "🌐 Choose the interface language:",
    "uk": "🌐 Обери мову інтерфейсу:",
}


async def show_settings(message, user_id, lang="ru"):
    user = await UserRepository().get(user_id)
    # One table for the filter's own words and the buttons that pick it: the same
    # three codes, so the prose and the row under it can never drift apart.
    gender = T.tr(T.SETTINGS_BUTTONS, lang).get(
        user.settings_gender_filter, T.tr(T.SETTINGS_BUTTONS, lang)["a"]
    )
    text = T.tr(SETTINGS_TEXT, lang).format(
        gender=gender, min=user.settings_age_min, max=user.settings_age_max
    )
    await send_rich(message.bot, message.chat.id, rich_settings_blocks(text, lang))


def rich_settings_blocks(text, lang):
    """Единый rich-стиль: заголовок+фильтр+возраст видимы, оговорка про
    Premium — в сворачиваемом <details>, выбор фильтра — кнопки-блоки."""
    labels = T.tr(T.SETTINGS_BUTTONS, lang)
    lines = text.split("\n")
    blocks = card_blocks("\n".join(lines[:4]))
    note = "\n".join(lines[4:]).strip()
    if note:
        blocks.append(details_block(T.tr(FILTER_NOTE_SUMMARY, lang), note))
    blocks += rich_buttons(
        [
            (labels["m"], "settings:gender:m", None),
            (labels["f"], "settings:gender:f", None),
            (labels["a"], "settings:gender:a", None),
            ("18-25", "settings:age:18-25", None),
            ("26-35", "settings:age:26-35", None),
            ("36+", "settings:age:36+", None),
            (labels["any"], "settings:age:any", None),
        ],
        per_row=2,
    )
    blocks += rich_buttons(
        [("🌐 Язык / Language", "language:menu", "primary")],
        per_row=1,
        center_tail=1,
    )
    return blocks


FILTER_NOTE_SUMMARY = {
    "ru": "📖 Что дают фильтры",
    "en": "📖 What the filters give",
    "uk": "📖 Що дають фільтри",
}


@settings_router.message(F.text.in_(T.all_labels(T.SETTINGS)))
async def cmd_settings(message: Message, lang: str = "ru"):
    await show_settings(message, message.from_user.id, lang)


@settings_router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await show_settings(callback.message, callback.from_user.id, lang)


def rich_language_blocks(lang, back_callback=None):
    """Единый rich-стиль: языки кнопками-блоками, «Назад» — по центру."""
    buttons = [
        (f"{T.LANGUAGE_FLAGS[code]} {T.LANGUAGE_NAMES[code]}", f"lang:{code}", None)
        for code in sorted(T.LANGUAGE_NAMES)
    ]
    blocks = card_blocks(T.tr(LANGUAGE_MENU_TEXT, lang))
    blocks += rich_buttons(buttons, per_row=2)
    if back_callback:
        blocks += rich_buttons(
            [(T.label(T.PAGE_PREV, lang), back_callback, None)],
            per_row=1,
            center_tail=1,
        )
    return blocks


@settings_router.message(Command("language"))
async def cmd_language(message: Message, lang: str = "ru"):
    await send_rich(message.bot, message.chat.id, rich_language_blocks(lang))


@settings_router.callback_query(F.data.startswith("language:menu"))
async def cb_language_menu(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    back_callback = (
        "profile" if callback.data == "language:menu:profile" else "settings"
    )
    await send_rich(
        callback.bot,
        callback.message.chat.id,
        rich_language_blocks(lang, back_callback),
    )


@settings_router.callback_query(F.data.startswith("settings:gender:"))
@settings_router.callback_query(F.data.startswith("settings:age:"))
async def cb_save(callback: CallbackQuery, lang: str = "ru"):
    parts = callback.data.split(":")
    if parts[1] == "gender":
        result = await SettingsService().save_search(
            callback.from_user.id, gender=parts[-1]
        )
    else:
        ages = {"18-25": (18, 25), "26-35": (26, 35), "36+": (36, 99), "any": (18, 99)}
        if parts[-1] not in ages:
            await callback.answer(T.tr(INVALID_RANGE, lang))
            return
        result = await SettingsService().save_search(
            callback.from_user.id, age_range=ages[parts[-1]]
        )
    if result.ok:
        await callback.answer(T.tr(SAVED, lang))
        await show_settings(callback.message, callback.from_user.id, lang)
        return
    if result.code == "PREMIUM_REQUIRED":
        await callback.answer(T.tr(NEED_ACCESS, lang))
        from handlers.premium import product_keyboard

        await callback.message.answer(
            T.tr(PICK_ACCESS, lang),
            reply_markup=await product_keyboard(
                ["gender_filter_one_match", "gender_filter_1m", "premium_1m"]
            ),
        )
        return
    await callback.answer(
        T.tr(ERRORS.get(result.code, GENERIC_FAIL), lang), show_alert=True
    )
