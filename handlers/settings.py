from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import settings_router
from keyboards.inline import settings_kb, menu_kb
from keyboards.reply import main_menu_kb
from utils.db import get_or_create_user, update_user, is_premium
import config


@settings_router.callback_query(F.data == "settings")
async def cb_settings(callback: CallbackQuery):
    db_user = await get_or_create_user(callback.from_user.id)
    premium = await is_premium(callback.from_user.id)

    gender_text = {"m": "👨 Мужчин", "f": "👩 Женщин", "a": "🌈 Всех"}.get(db_user.settings_gender_filter, "🌈 Всех")
    age_text = f"{db_user.settings_age_min}–{db_user.settings_age_max}" if db_user.settings_age_max < 99 else "Любой"

    text = (
        f"⚙️ Кого искать\n\n"
        f"Текущий фильтр: {gender_text}\n"
        f"Возраст: {age_text}\n"
    )

    if not premium:
        text += (
            f"\n🔒 Фильтр по полу — Premium-функция\n"
            f"[ ⭐ Разблокировать за {config.STARS_GENDER_FILTER}⭐ ]\n"
        )

    await callback.message.edit_text(text, reply_markup=settings_kb())
    await callback.answer()


@settings_router.message(F.text == "⚙️ Настройки поиска")
async def cmd_settings(message: Message):
    db_user = await get_or_create_user(message.from_user.id)
    premium = await is_premium(message.from_user.id)

    gender_text = {"m": "👨 Мужчин", "f": "👩 Женщин", "a": "🌈 Всех"}.get(db_user.settings_gender_filter, "🌈 Всех")
    age_text = f"{db_user.settings_age_min}–{db_user.settings_age_max}" if db_user.settings_age_max < 99 else "Любой"

    text = f"⚙️ Кого искать\n\nТекущий фильтр: {gender_text}\nВозраст: {age_text}"

    if not premium:
        text += f"\n\n🔒 Фильтр по полу — Premium-функция"

    await message.answer(text, reply_markup=settings_kb())


@settings_router.callback_query(F.data.startswith("settings:gender:"))
async def cb_set_gender_filter(callback: CallbackQuery):
    gender = callback.data.split(":")[-1]
    premium = await is_premium(callback.from_user.id)

    if gender != "a" and not premium:
        # Offer to pay
        from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
        await callback.message.edit_text(
            f"🔒 Фильтр по полу — Premium-функция\n\n"
            f"Разблокировать за {config.STARS_GENDER_FILTER}⭐?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐ Купить за {config.STARS_GENDER_FILTER}⭐", callback_data="settings:buy_gender")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
            ]),
        )
        await callback.answer()
        return

    await update_user(callback.from_user.id, settings_gender_filter=gender)
    await callback.answer("Фильтр сохранён!")
    await cb_settings(callback)


@settings_router.callback_query(F.data == "settings:buy_gender")
async def cb_buy_gender(callback: CallbackQuery):
    premium = await is_premium(callback.from_user.id)
    if premium:
        await callback.answer("У тебя Premium — фильтр уже доступен!", show_alert=True)
        return

    from aiogram.types import LabeledPrice
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Фильтр по полу",
        description="Разблокировать фильтр по полу навсегда",
        payload=f"gender_filter:{callback.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Фильтр по полу", amount=config.STARS_GENDER_FILTER)],
    )
    await callback.answer()


@settings_router.callback_query(F.data.startswith("settings:age:"))
async def cb_set_age_filter(callback: CallbackQuery):
    age_range = callback.data.split(":")[-1]
    age_map = {
        "18-25": (18, 25),
        "26-35": (26, 35),
        "36+": (36, 99),
        "any": (18, 99),
    }
    min_age, max_age = age_map.get(age_range, (18, 99))
    await update_user(callback.from_user.id, settings_age_min=min_age, settings_age_max=max_age)
    await callback.answer("Возрастной диапазон сохранён!")
    await cb_settings(callback)
