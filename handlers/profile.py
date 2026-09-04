from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import profile_router
from keyboards.inline import profile_kb, profile_edit_kb, menu_kb
from keyboards.reply import main_menu_kb
from utils.db import get_or_create_user, get_user_stats


def format_profile(stats: dict) -> str:
    # BUG FIX: handle None premium_expires even if is_premium is True
    if stats.get("is_premium") and stats.get("premium_expires"):
        premium_text = "✅ до " + stats["premium_expires"].strftime("%d.%m.%Y")
    else:
        premium_text = "нет"
    return (
        f"👤 Твой профиль\n\n"
        f"🆔 ID: {stats['anon_id']}\n"
        f"📅 В боте с: {stats['created_at'].strftime('%d.%m.%Y')}\n\n"
        f"💬 Диалогов: {stats['chats_count']}\n"
        f"👍 Лайков: {stats['likes']}   👎 Дизлайков: {stats['dislikes']}\n"
        f"⭐ Premium: {premium_text}\n"
    )


@profile_router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    stats = await get_user_stats(callback.from_user.id)
    if not stats:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    await callback.message.edit_text(format_profile(stats), reply_markup=profile_kb())
    await callback.answer()


@profile_router.message(F.text == "👤 Мой профиль")
async def cmd_profile(message: Message):
    stats = await get_user_stats(message.from_user.id)
    if not stats:
        await message.answer("Профиль не найден.")
        return
    await message.answer(format_profile(stats), reply_markup=profile_kb())


@profile_router.callback_query(F.data == "profile:edit")
async def cb_profile_edit(callback: CallbackQuery):
    await callback.message.edit_text(
        "✏️ Заполнить о себе\n\n"
        "Можешь по желанию указать — это поможет находить более подходящих собеседников.\n"
        "Всё можно оставить пустым.",
        reply_markup=profile_edit_kb(),
    )
    await callback.answer()


@profile_router.callback_query(F.data == "profile:set_gender")
async def cb_set_gender(callback: CallbackQuery):
    from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
    await callback.message.edit_text(
        "👤 Выбери свой пол:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Мужской", callback_data="profile:gender:m"),
                InlineKeyboardButton(text="👩 Женский", callback_data="profile:gender:f"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:edit")],
        ]),
    )
    await callback.answer()


@profile_router.callback_query(F.data.startswith("profile:gender:"))
async def cb_save_gender(callback: CallbackQuery):
    gender = callback.data.split(":")[-1]
    from utils.db import update_user
    await update_user(callback.from_user.id, gender=gender)
    await callback.answer("Пол сохранён!", show_alert=True)
    await cb_profile(callback)


@profile_router.callback_query(F.data == "profile:set_age")
async def cb_set_age(callback: CallbackQuery):
    from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
    ages = [
        [InlineKeyboardButton(text=str(a), callback_data=f"profile:age:{a}")]
        for a in range(18, 60, 3)
    ]
    ages.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:edit")])
    await callback.message.edit_text("🎂 Выбери возраст:", reply_markup=InlineKeyboardMarkup(inline_keyboard=ages))
    await callback.answer()


@profile_router.callback_query(F.data.startswith("profile:age:"))
async def cb_save_age(callback: CallbackQuery):
    age = int(callback.data.split(":")[-1])
    from utils.db import update_user
    await update_user(callback.from_user.id, age=age)
    await callback.answer(f"Возраст {age} сохранён!", show_alert=True)
    await cb_profile(callback)


@profile_router.callback_query(F.data == "profile:delete")
async def cb_delete_profile(callback: CallbackQuery):
    from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
    await callback.message.edit_text(
        "🗑 Удалить аккаунт?\n\nЭто действие необратимо!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Да, удалить", callback_data="profile:delete:confirm")],
            [InlineKeyboardButton(text="⬅️ Нет, назад", callback_data="profile")],
        ]),
    )
    await callback.answer()


@profile_router.callback_query(F.data == "profile:delete:confirm")
async def cb_delete_confirm(callback: CallbackQuery):
    from utils.db import update_user
    await update_user(callback.from_user.id, is_banned=True)
    await callback.message.edit_text("Аккаунт удалён. /start чтобы начать заново.")
    await callback.answer()
