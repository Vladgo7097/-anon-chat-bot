from aiogram import F
from aiogram.types import Message, CallbackQuery
from handlers import settings_router
from keyboards import texts as T
from keyboards.inline import settings_kb
from repositories.users import UserRepository
from repositories.chats import premium_active
from services.settings import SettingsService


async def show_settings(message, user_id):
    user = await UserRepository().get(user_id)
    gender = {"m": "Мужчин", "f": "Женщин", "a": "Всех"}.get(user.settings_gender_filter, "Всех")
    await message.answer(f"<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Кого искать\n\nФильтр: {gender}\nВозраст: {user.settings_age_min}–{user.settings_age_max}\n\nФильтр по полу требует Premium или отдельной покупки; возрастной диапазон — Premium. После истечения доступа настройки сохраняются, но не применяются.", reply_markup=settings_kb())


@settings_router.message(F.text == T.SETTINGS)
async def cmd_settings(message: Message):
    await show_settings(message, message.from_user.id)


@settings_router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):
    await callback.answer()
    await show_settings(callback.message, callback.from_user.id)


@settings_router.callback_query(F.data.startswith("settings:gender:"))
@settings_router.callback_query(F.data.startswith("settings:age:"))
async def cb_save(callback: CallbackQuery):
    parts = callback.data.split(":")
    if parts[1] == "gender":
        result = await SettingsService().save_search(callback.from_user.id, gender=parts[-1])
    else:
        ages = {"18-25": (18,25), "26-35": (26,35), "36+": (36,99), "any": (18,99)}
        if parts[-1] not in ages:
            await callback.answer("Недопустимый диапазон.")
            return
        result = await SettingsService().save_search(callback.from_user.id, age_range=ages[parts[-1]])
    if result.ok:
        await callback.answer("Сохранено")
        await show_settings(callback.message, callback.from_user.id)
        return
    if result.code == "PREMIUM_REQUIRED":
        await callback.answer("Нужен доступ к фильтру.")
        from handlers.premium import product_keyboard
        await callback.message.answer("Выбери доступ к фильтрам:", reply_markup=await product_keyboard(["gender_filter_one_match", "gender_filter_1m", "premium_1m"]))
        return
    await callback.answer({"ACTIVE_CHAT": "Настройки нельзя менять во время диалога. Сначала заверши его.",
                            "NOT_FOUND": "Профиль не найден. Попробуй /start."}.get(result.code, "Не удалось сохранить."), show_alert=True)
