from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import json
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
    link=f"https://t.me/{bot.username}?start=ref_{user.referral_code}"
    names={"first_chat":"Первый диалог","50_chats":"50 диалогов","100_chats":"100 диалогов","streak_7":"7 дней подряд","streak_30":"30 дней подряд","reveal_1":"Взаимное раскрытие","reveal_5":"5 взаимных раскрытий"}
    text=f"<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Твои награды\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Приглашено: {user.referrals_count} | Активировали: {user.referrals_activated}\nПригласи друга: после его первого диалога оба получите скидку.\n{link}\n\n<tg-emoji emoji-id=\"5303040337059543238\">🔥</tg-emoji> Стрик: {streak} дней (UTC)\n3 дня: −10% на разовую покупку\n7 дней: −20% на Premium\n30 дней: −30% на Premium 12 месяцев\n\n<tg-emoji emoji-id=\"5283130719806175947\">🏆</tg-emoji> Достижения:\n"
    text+="\n".join(("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> " if code in achievements else "<tg-emoji emoji-id=\"5312229088876847138\">🔒</tg-emoji> ")+label for code,label in names.items())
    text+="".join(f"\n<tg-emoji emoji-id=\"5872889730240089575\">🏅</tg-emoji> Событие: {escape(code.removeprefix('season:'))}" for code in achievements if code.startswith("season:"))
    text+="\n\nСкидки автоматически учитываются в счёте, без суммирования:\n"
    text+="\n".join(f"−{o.discount_percent}% · {escape(o.product_scope)} · до {o.ends_at:%d.%m.%Y %H:%M} UTC" for o in offers) or "Пока нет активных скидок."
    await callback.answer()
    await callback.message.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Поделиться ссылкой", icon_custom_emoji_id="5967432491684860012",url="https://t.me/share/url?url="+quote(link,safe="")),
        InlineKeyboardButton(text="Premium", icon_custom_emoji_id="5920281855378068765",callback_data="premium")]]))


# Telegram wants a custom emoji's fallback to be that emoji's own character,
# so each id travels together with the character it stands for.
GENDER_ICONS = {"male": ("5366121041426922772", "👨‍🦳"),
                "female": ("5366209534933089956", "👵")}
UNKNOWN_GENDER_ICON = ("5348232622898167572", "♥️")


async def show_profile(message, user_id):
    s = await get_user_stats(user_id)
    if not s:
        await message.answer("Создай профиль: /start")
        return
    gender = {"male": "Парень", "female": "Девушка"}.get(s['gender'], "Не указан")
    gender_icon = GENDER_ICONS.get(s['gender'], UNKNOWN_GENDER_ICON)
    premium = f"до {s['premium_expires']:%d.%m.%Y}" if s['is_premium'] else "нет"
    await message.answer(f"<tg-emoji emoji-id=\"5976585328985643006\">🧑‍🧑‍🧒</tg-emoji> Твой профиль\n\n<tg-emoji emoji-id=\"5965485570124681987\">🆔</tg-emoji> ID: {s['anon_id']}\n<tg-emoji emoji-id=\"{gender_icon[0]}\">{gender_icon[1]}</tg-emoji> Пол: {gender}\n<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Возраст: {s['age']}\n<tg-emoji emoji-id=\"5258105663359294787\">📅</tg-emoji> В боте с: {s['created_at']:%d.%m.%Y}\n\n<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Диалогов проведено: {s['chats_count']}\n<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Лайков: {s['likes']}   <tg-emoji emoji-id=\"5348132683304156113\">👎</tg-emoji> Дизлайков: {s['dislikes']}\n<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> Premium: {premium}", reply_markup=profile_kb())


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
    await callback.message.answer({"GENDER": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Выбери пол:", "AGE": "🎂 Выбери возраст:", "PHOTO": "<tg-emoji emoji-id=\"5258254475386167466\">🖼</tg-emoji> Отправь фото профиля. Оно будет показано собеседнику только после взаимного раскрытия."}[step],
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
        result = await ProfileService().edit(message.from_user.id, "age", int(text) if text.isascii() and text.isdigit() else None)
    if result.ok:
        await state.clear()
        await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Сохранено", reply_markup=main_menu_kb())
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
    await callback.message.answer("<tg-emoji emoji-id=\"5312229088876847138\">🗑</tg-emoji> Удалить профиль и фотографии? Premium будет отключён. Платёжные записи, жалобы и служебный аудит сохраняются для учёта и рассмотрения обращений.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Да, удалить", icon_custom_emoji_id="5312229088876847138", callback_data="profile:delete:confirm", style="danger"),
            InlineKeyboardButton(text="Отмена", callback_data="profile")]]))


@profile_router.callback_query(F.data == "profile:delete:confirm")
async def cb_confirm_delete(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() != ProfileEdit.DELETE_CONFIRM.state:
        await callback.answer("Подтверждение устарело.")
        return
    await ProfileService().delete(callback.from_user.id)
    await state.clear()
    await callback.answer()
    await callback.message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Профиль удалён. Вернуться можно через /start.")


@profile_router.callback_query(F.data.in_({"profile:privacy", "profile:push:on", "profile:push:off"}))
async def cb_privacy(callback: CallbackQuery, state: FSMContext):
    from repositories.users import UserRepository
    user = await UserRepository().get(callback.from_user.id)
    if not user:
        await callback.answer("Профиль не найден. Открой /start.", show_alert=True)
        return
    enabled = json.loads(user.notification_settings or "{}").get("marketing", True)
    if callback.data != "profile:privacy":
        enabled = callback.data.endswith(":on")
        result = await ProfileService().marketing(callback.from_user.id, enabled)
        if not result.ok:
            await callback.answer("Не удалось сохранить настройку. Открой /start.", show_alert=True)
            return
    await state.clear()
    await callback.answer("Сохранено" if callback.data != "profile:privacy" else None)
    text = (
        "🔒 Приватность\n\n"
        "Собеседник не видит твой Telegram ID, имя и username. "
        "Username раскрывается только после согласия обоих участников. "
        "Фото профиля показывается только после взаимного согласия раскрыться.\n\n"
        "Данные, которые ты сам отправляешь в сообщениях, видны собеседнику. "
        "Последние текстовые сообщения временно хранятся до 30 минут для возможной жалобы; "
        "в базу они попадают только после явной отправки жалобы.\n\n"
        f"Рекомендации и предложения: {'включены' if enabled else 'выключены'}.\n"
        "Этот переключатель управляет рекламными и маркетинговыми уведомлениями. "
        "Сообщения о диалогах, оплатах и другие служебные уведомления остаются."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Выключить уведомления" if enabled else "Включить уведомления",
            callback_data="profile:push:off" if enabled else "profile:push:on", style="primary"),
    ], [InlineKeyboardButton(text="Назад в профиль", callback_data="profile")]])
    if callback.data == "profile:privacy":
        await callback.message.answer(text, reply_markup=keyboard)
    else:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message:
                raise
