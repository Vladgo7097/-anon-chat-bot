from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from handlers import profile_router
from keyboards.inline import profile_kb, profile_edit_kb
from keyboards.reply import onboarding_kb, main_menu_kb
from keyboards import texts as T
from services.profile import ProfileService
from utils.db import get_user_stats


class ProfileEdit(StatesGroup):
    GENDER = State()
    AGE = State()
    PHOTO = State()
    DELETE_CONFIRM = State()


@profile_router.callback_query(F.data == "profile:rewards")
async def cb_rewards(callback: CallbackQuery):
    from services.growth import GrowthService, StreakService
    from html import escape
    from urllib.parse import quote
    streak=await StreakService().checkin(callback.from_user.id)
    user,achievements,offers=await GrowthService().profile(callback.from_user.id)
    bot=await callback.bot.get_me()
    link=f"https://t.me/{bot.username}?start=ref_{user.anon_id.lstrip('#')}"
    names={"first_chat":"Первый диалог","50_chats":"50 диалогов","100_chats":"100 диалогов","streak_7":"7 дней подряд","streak_30":"30 дней подряд","reveal_1":"Взаимное раскрытие","reveal_5":"5 взаимных раскрытий"}
    text=f"🎁 Твои награды\n\n👥 Приглашено: {user.referrals_count} | Активировали: {user.referrals_activated}\nПригласи друга: после его первого диалога оба получите скидку.\n{link}\n\n🔥 Стрик: {streak} дней (UTC)\n3 дня: −10% на разовую покупку\n7 дней: −20% на Premium\n30 дней: −30% на Premium 12 месяцев\n\n🏆 Достижения:\n"
    text+="\n".join(("✅ " if code in achievements else "🔒 ")+label for code,label in names.items())
    text+="".join(f"\n🏅 Событие: {escape(code.removeprefix('season:'))}" for code in achievements if code.startswith("season:"))
    text+="\n\nСкидки автоматически учитываются в счёте, без суммирования:\n"
    text+="\n".join(f"−{o.discount_percent}% · {escape(o.product_scope)} · до {o.ends_at:%d.%m.%Y %H:%M} UTC" for o in offers) or "Пока нет активных скидок."
    await callback.answer()
    await callback.message.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Поделиться ссылкой",url="https://t.me/share/url?url="+quote(link,safe="")),
        InlineKeyboardButton(text="⭐ Premium",callback_data="premium")]]))


async def show_profile(message, user_id):
    s = await get_user_stats(user_id)
    if not s:
        await message.answer("Создай профиль: /start")
        return
    gender = {"male": "Парень", "female": "Девушка"}.get(s['gender'], "Не указан")
    premium = f"до {s['premium_expires']:%d.%m.%Y}" if s['is_premium'] else "нет"
    await message.answer(f"👤 Твой профиль\n\n🆔 ID: {s['anon_id']}\n👤 Пол: {gender}\n<emoji document_id=\"5452055425690123301\">🎂</emoji> Возраст: {s['age']}\n📅 В боте с: {s['created_at']:%d.%m.%Y}\n\n💬 Диалогов проведено: {s['chats_count']}\n👍 Лайков: {s['likes']}   👎 Дизлайков: {s['dislikes']}\n⭐ Premium: {premium}", reply_markup=profile_kb())


@profile_router.message(Command("profile"))
@profile_router.message(F.text == T.PROFILE)
async def cmd_profile(message: Message, state: FSMContext):
    await state.clear()
    await show_profile(message, message.from_user.id)


@profile_router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await show_profile(callback.message, callback.from_user.id)


@profile_router.callback_query(F.data == "profile:edit")
async def cb_edit(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("Что хочешь изменить?", reply_markup=profile_edit_kb())


@profile_router.callback_query(F.data.in_({"profile:set_gender", "profile:set_age", "profile:photo"}))
async def cb_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":")[-1]
    step = {"set_gender": "GENDER", "set_age": "AGE", "photo": "PHOTO"}[field]
    await state.set_state(getattr(ProfileEdit, step))
    await state.update_data(age_page=0)
    await callback.answer()
    await callback.message.answer({"GENDER": "👤 Выбери пол:", "AGE": "🎂 Выбери возраст:", "PHOTO": "🖼 Отправь фото профиля. Собеседникам оно автоматически не показывается."}[step],
        reply_markup=onboarding_kb(step) if step != "PHOTO" else main_menu_kb())


@profile_router.message(ProfileEdit.GENDER)
@profile_router.message(ProfileEdit.AGE)
@profile_router.message(ProfileEdit.PHOTO)
async def save_field(message: Message, state: FSMContext):
    step = (await state.get_state()).split(":")[-1]
    text = message.text or ""
    if text in T.SYSTEM_TEXTS - {T.MALE, T.FEMALE, T.PAGE_NEXT, T.PAGE_PREV}:
        await state.clear()
        await message.answer("Редактирование отменено. Выбери пункт меню.", reply_markup=main_menu_kb())
        return
    if step == "PHOTO":
        if not message.photo:
            await message.answer("Отправь фотографию.")
            return
        result = await ProfileService().add_photo(message.from_user.id, message.photo[-1].file_id)
    elif step == "GENDER":
        result = await ProfileService().edit(message.from_user.id, "gender", {T.MALE: "male", T.FEMALE: "female"}.get(text))
    else:
        if text in {T.PAGE_NEXT, T.PAGE_PREV}:
            page = (await state.get_data()).get("age_page", 0)
            page = max(0, min(6, page + (1 if text == T.PAGE_NEXT else -1)))
            await state.update_data(age_page=page)
            await message.answer("🎂 Выбери возраст:", reply_markup=onboarding_kb("AGE", page))
            return
        result = await ProfileService().edit(message.from_user.id, "age", int(text) if text.isdigit() else None)
    if result.ok:
        await state.clear()
        await message.answer("✅ Сохранено", reply_markup=main_menu_kb())
        await show_profile(message, message.from_user.id)
    elif result.code == "PHOTO_LIMIT":
        from handlers.premium import product_keyboard
        await message.answer("Достигнут лимит фото. Дополнительное место доступно за Stars или с Premium (до 3 фото).", reply_markup=await product_keyboard(["extra_profile_photo", "premium_1m"]))
    else:
        await message.answer("Проверь данные. Возраст — от 18 до 99 лет. Изменения во время чата недоступны.")


@profile_router.callback_query(F.data == "profile:delete")
async def cb_delete(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileEdit.DELETE_CONFIRM)
    await callback.answer()
    await callback.message.answer("🗑 Удалить профиль и фотографии? Premium будет отключён. Платёжные записи, жалобы и служебный аудит сохраняются для учёта и рассмотрения обращений.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Да, удалить", callback_data="profile:delete:confirm", style="danger"),
            InlineKeyboardButton(text="⬅️ Отмена", callback_data="profile")]]))


@profile_router.callback_query(F.data == "profile:delete:confirm")
async def cb_confirm_delete(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ProfileEdit.DELETE_CONFIRM.state:
        await callback.answer("Подтверждение устарело.")
        return
    await ProfileService().delete(callback.from_user.id)
    await state.clear()
    await callback.answer()
    await callback.message.answer("✅ Профиль удалён. Вернуться можно через /start.")


@profile_router.callback_query(F.data.in_({"profile:privacy", "profile:push:on", "profile:push:off"}))
async def cb_privacy(callback: CallbackQuery):
    if callback.data != "profile:privacy":
        await ProfileService().marketing(callback.from_user.id, callback.data.endswith(":on"))
    await callback.answer()
    await callback.message.answer("🔒 Telegram ID скрыт. Контакт раскрывается только после взаимного согласия.\nНастрой маркетинговые уведомления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Включить", callback_data="profile:push:on", style="success"),
            InlineKeyboardButton(text="Выключить", callback_data="profile:push:off")]]))
