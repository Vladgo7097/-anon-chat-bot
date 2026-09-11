from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from handlers import settings_router
from keyboards import texts as T
from keyboards.inline import settings_kb, language_kb
from repositories.users import UserRepository
from repositories.chats import premium_active
from services.settings import SettingsService

GENDER_LABELS = {
    "ru": {"m": "Мужчин", "f": "Женщин", "a": "Всех"},
    "en": {"m": "Men", "f": "Women", "a": "Everyone"},
    "uk": {"m": "Чоловіків", "f": "Жінок", "a": "Усіх"},
}
SETTINGS_TEXT = {
    "ru": "<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Кого искать\n\nФильтр: {gender}\nВозраст: {min}–{max}\n\nФильтр по полу требует Premium или отдельной покупки; возрастной диапазон — Premium. После истечения доступа настройки сохраняются, но не применяются.",
    "en": "<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Who to search for\n\nFilter: {gender}\nAge: {min}–{max}\n\nThe gender filter needs Premium or a separate purchase; the age range needs Premium. Settings stay saved after access expires, but stop applying.",
    "uk": "<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Кого шукати\n\nФільтр: {gender}\nВік: {min}–{max}\n\nФільтр за статтю потребує Premium або окремої покупки; віковий діапазон — Premium. Після завершення доступу налаштування зберігаються, але не застосовуються.",
}
SAVED = {"ru": "Сохранено", "en": "Saved", "uk": "Збережено"}
INVALID_RANGE = {"ru": "Недопустимый диапазон.", "en": "Invalid range.", "uk": "Недопустимий діапазон."}
NEED_ACCESS = {"ru": "Нужен доступ к фильтру.", "en": "This filter needs access.", "uk": "Потрібен доступ до фільтра."}
PICK_ACCESS = {"ru": "Выбери доступ к фильтрам:", "en": "Choose access to filters:", "uk": "Обери доступ до фільтрів:"}
ERRORS = {
    "ACTIVE_CHAT": {"ru": "Настройки нельзя менять во время диалога. Сначала заверши его.",
                    "en": "Settings can't be changed during a chat. Finish it first.",
                    "uk": "Налаштування не можна змінювати під час діалогу. Спочатку заверши його."},
    "NOT_FOUND": {"ru": "Профиль не найден. Попробуй /start.", "en": "Profile not found. Try /start.",
                  "uk": "Профіль не знайдено. Спробуй /start."},
}
GENERIC_FAIL = {"ru": "Не удалось сохранить.", "en": "Could not save.", "uk": "Не вдалося зберегти."}
LANGUAGE_MENU_TEXT = {"ru": "🌐 Выбери язык интерфейса:", "en": "🌐 Choose the interface language:", "uk": "🌐 Обери мову інтерфейсу:"}


async def show_settings(message, user_id, lang="ru"):
    user = await UserRepository().get(user_id)
    gender = T.tr(GENDER_LABELS, lang).get(user.settings_gender_filter, T.tr(GENDER_LABELS, lang)["a"])
    text = T.tr(SETTINGS_TEXT, lang).format(gender=gender, min=user.settings_age_min, max=user.settings_age_max)
    await message.answer(text, reply_markup=settings_kb())


@settings_router.message(F.text.in_(T.all_labels(T.SETTINGS)))
async def cmd_settings(message: Message, lang: str = "ru"):
    await show_settings(message, message.from_user.id, lang)


@settings_router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await show_settings(callback.message, callback.from_user.id, lang)


@settings_router.message(Command("language"))
async def cmd_language(message: Message, lang: str = "ru"):
    await message.answer(T.tr(LANGUAGE_MENU_TEXT, lang), reply_markup=language_kb())


@settings_router.callback_query(F.data == "language:menu")
async def cb_language_menu(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await callback.message.answer(T.tr(LANGUAGE_MENU_TEXT, lang), reply_markup=language_kb(back_callback="settings"))


@settings_router.callback_query(F.data.startswith("settings:gender:"))
@settings_router.callback_query(F.data.startswith("settings:age:"))
async def cb_save(callback: CallbackQuery, lang: str = "ru"):
    parts = callback.data.split(":")
    if parts[1] == "gender":
        result = await SettingsService().save_search(callback.from_user.id, gender=parts[-1])
    else:
        ages = {"18-25": (18,25), "26-35": (26,35), "36+": (36,99), "any": (18,99)}
        if parts[-1] not in ages:
            await callback.answer(T.tr(INVALID_RANGE, lang))
            return
        result = await SettingsService().save_search(callback.from_user.id, age_range=ages[parts[-1]])
    if result.ok:
        await callback.answer(T.tr(SAVED, lang))
        await show_settings(callback.message, callback.from_user.id, lang)
        return
    if result.code == "PREMIUM_REQUIRED":
        await callback.answer(T.tr(NEED_ACCESS, lang))
        from handlers.premium import product_keyboard
        await callback.message.answer(T.tr(PICK_ACCESS, lang), reply_markup=await product_keyboard(["gender_filter_one_match", "gender_filter_1m", "premium_1m"]))
        return
    await callback.answer(T.tr(ERRORS.get(result.code, GENERIC_FAIL), lang), show_alert=True)
