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
    BIO = State()
    PHOTO = State()
    DELETE_CONFIRM = State()


# Every word this branch speaks, keyed by language first so tr() picks a row the
# way it does for the rest of the bot. The profile was the one main-menu button
# that still answered in Russian whatever language the client had asked in: the
# card, the edit prompts, the privacy screen and each toast under them are one
# table now, so adding a string means adding three translations together.
P = {
    "ru": {
        "create": "Создай профиль: /start",
        "gender": {"male": "Парень", "female": "Девушка", "none": "Не указан"},
        "premium_until": "до {d}", "premium_none": "нет",
        "until": "до {d}",
        "photos": "Фото: {v}", "bio": "О себе: {v}",
        "card": "<tg-emoji emoji-id=\"5976585328985643006\">🧑‍🧑‍🧒</tg-emoji> Твой профиль\n\n<tg-emoji emoji-id=\"5965485570124681987\">🆔</tg-emoji> ID: {anon}\n<tg-emoji emoji-id=\"{gicon}\">{gemoji}</tg-emoji> Пол: {gender}\n<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Возраст: {age}\n<tg-emoji emoji-id=\"5258105663359294787\">📅</tg-emoji> В боте с: {joined}{photos}{bio}\n\n<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Диалогов проведено: {chats}\n<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Лайков: {likes}   <tg-emoji emoji-id=\"5348132683304156113\">👎</tg-emoji> Дизлайков: {dislikes}\n<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> Premium: {premium}",
        "edit_menu": "Что хочешь изменить?",
        "ask_gender": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Выбери пол:",
        "ask_age": "🎂 Выбери возраст:",
        "ask_bio": "<tg-emoji emoji-id=\"5301173701323028420\">✏️</tg-emoji> Пару слов о себе, до 150 символов. Видно собеседнику в профиле. Пришли «-», чтобы очистить:",
        "ask_photo": "<tg-emoji emoji-id=\"5258254475386167466\">🖼</tg-emoji> Отправь фото профиля. Оно будет показано собеседнику только после взаимного раскрытия.",
        "need_photo": "Отправь фотографию.",
        "need_bio": "Отправь текст, или «-», чтобы очистить.",
        "bio_too_long": "Слишком длинно — до 150 символов. Попробуй короче.",
        "cancelled": "Редактирование отменено. Выбери пункт меню.",
        "saved": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Сохранено",
        "photo_limit": "Достигнут лимит фото. Дополнительное место доступно за Stars или с Premium (до 3 фото).",
        "invalid": "Проверь данные. Возраст — от 18 до 99 лет. Изменения во время чата недоступны.",
        "photo_deleted": "Фото удалено",
        "photo_missing": "Фото не найдено",
        "delete_ask": "<tg-emoji emoji-id=\"5312229088876847138\">🗑</tg-emoji> Удалить профиль и фотографии? Premium будет отключён. Платёжные записи, жалобы и служебный аудит сохраняются для учёта и рассмотрения обращений.",
        "delete_yes": "Да, удалить",
        "delete_no": "Отмена",
        "delete_stale": "Подтверждение устарело.",
        "delete_done": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Профиль удалён. Вернуться можно через /start.",
        "profile_missing": "Профиль не найден. Открой /start.",
        "save_failed": "Не удалось сохранить настройку. Открой /start.",
        "saved_toast": "Сохранено",
        "privacy": "🔒 Приватность\n\nСобеседник не видит твой Telegram ID, имя и username. Username раскрывается только после согласия обоих участников. Фото профиля показывается только после взаимного согласия раскрыться.\n\nДанные, которые ты сам отправляешь в сообщениях, видны собеседнику. Последние текстовые сообщения временно хранятся до 30 минут для возможной жалобы; в базу они попадают только после явной отправки жалобы.\n\nРекомендации и предложения: {recs}.\nЭтот переключатель управляет рекламными и маркетинговыми уведомлениями. Сообщения о диалогах, оплатах и другие служебные уведомления остаются.",
        "recs_on": "включены", "recs_off": "выключены",
        "push_off": "Выключить уведомления", "push_on": "Включить уведомления",
        "back_profile": "Назад в профиль",
        "rewards": "<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Твои награды\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Приглашено: {invited} | Активировали: {activated}\nПригласи друга: после его первого диалога ты получишь час Premium, друг — скидку 20%.\n{link}\n\n<tg-emoji emoji-id=\"5303040337059543238\">🔥</tg-emoji> Стрик: {streak} дней (UTC)\n3 дня: −10% на разовую покупку\n7 дней: −20% на Premium\n30 дней: −30% на Premium 12 месяцев\n\n<tg-emoji emoji-id=\"5283130719806175947\">🏆</tg-emoji> Достижения:\n",
        "event": "Событие: {code}",
        "discounts_note": "Скидки автоматически учитываются в счёте, без суммирования:",
        "no_discounts": "Пока нет активных скидок.",
        "share": "Поделиться ссылкой",
        "achievements": {"first_chat": "Первый диалог", "50_chats": "50 диалогов",
                         "100_chats": "100 диалогов", "streak_7": "7 дней подряд",
                         "streak_30": "30 дней подряд", "reveal_1": "Взаимное раскрытие",
                         "reveal_5": "5 взаимных раскрытий"},
    },
    "en": {
        "create": "Create a profile: /start",
        "gender": {"male": "Man", "female": "Woman", "none": "Not set"},
        "premium_until": "until {d}", "premium_none": "none",
        "until": "until {d}",
        "photos": "Photos: {v}", "bio": "About: {v}",
        "card": "<tg-emoji emoji-id=\"5976585328985643006\">🧑‍🧑‍🧒</tg-emoji> Your profile\n\n<tg-emoji emoji-id=\"5965485570124681987\">🆔</tg-emoji> ID: {anon}\n<tg-emoji emoji-id=\"{gicon}\">{gemoji}</tg-emoji> Gender: {gender}\n<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Age: {age}\n<tg-emoji emoji-id=\"5258105663359294787\">📅</tg-emoji> Joined: {joined}{photos}{bio}\n\n<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Chats: {chats}\n<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Likes: {likes}   <tg-emoji emoji-id=\"5348132683304156113\">👎</tg-emoji> Dislikes: {dislikes}\n<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> Premium: {premium}",
        "edit_menu": "What do you want to change?",
        "ask_gender": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Pick a gender:",
        "ask_age": "🎂 Pick an age:",
        "ask_bio": "<tg-emoji emoji-id=\"5301173701323028420\">✏️</tg-emoji> A few words about you, up to 150 characters. Your match sees it in the profile. Send «-» to clear it:",
        "ask_photo": "<tg-emoji emoji-id=\"5258254475386167466\">🖼</tg-emoji> Send a profile photo. It reaches your match only after you both reveal.",
        "need_photo": "Send a photo.",
        "need_bio": "Send some text, or «-» to clear it.",
        "bio_too_long": "Too long — 150 characters max. Try something shorter.",
        "cancelled": "Edit cancelled. Pick a menu item.",
        "saved": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Saved",
        "photo_limit": "Photo limit reached. An extra slot costs Stars or comes with Premium (up to 3 photos).",
        "invalid": "Check the values. Age is 18 to 99. Changes are unavailable during a chat.",
        "photo_deleted": "Photo deleted",
        "photo_missing": "No photo found",
        "delete_ask": "<tg-emoji emoji-id=\"5312229088876847138\">🗑</tg-emoji> Delete the profile and its photos? Premium turns off. Payment records, reports and the service audit stay for accounting and appeal handling.",
        "delete_yes": "Yes, delete",
        "delete_no": "Cancel",
        "delete_stale": "Confirmation expired.",
        "delete_done": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Profile deleted. You can come back with /start.",
        "profile_missing": "Profile not found. Open /start.",
        "save_failed": "Could not save the setting. Open /start.",
        "saved_toast": "Saved",
        "privacy": "🔒 Privacy\n\nYour match cannot see your Telegram ID, name or username. The username is revealed only when both sides agree. The profile photo shows only after mutual consent to reveal.\n\nWhatever you send in messages is visible to your match. Recent text is held for up to 30 minutes in case of a report; it reaches the database only after a report is actually filed.\n\nTips and offers: {recs}.\nThis switch controls advertising and marketing notifications. Messages about chats, payments and other service notices still arrive.",
        "recs_on": "on", "recs_off": "off",
        "push_off": "Turn notifications off", "push_on": "Turn notifications on",
        "back_profile": "Back to profile",
        "rewards": "<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Your rewards\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Invited: {invited} | Activated: {activated}\nInvite a friend: after their first chat you get an hour of Premium, they get 20% off.\n{link}\n\n<tg-emoji emoji-id=\"5303040337059543238\">🔥</tg-emoji> Streak: {streak} days (UTC)\n3 days: −10% off a one-time purchase\n7 days: −20% off Premium\n30 days: −30% off Premium 12 months\n\n<tg-emoji emoji-id=\"5283130719806175947\">🏆</tg-emoji> Achievements:\n",
        "event": "Event: {code}",
        "discounts_note": "Discounts apply automatically to the total, and do not stack:",
        "no_discounts": "No active discounts yet.",
        "share": "Share the link",
        "achievements": {"first_chat": "First chat", "50_chats": "50 chats",
                         "100_chats": "100 chats", "streak_7": "7 days in a row",
                         "streak_30": "30 days in a row", "reveal_1": "Mutual reveal",
                         "reveal_5": "5 mutual reveals"},
    },
    "uk": {
        "create": "Створи профіль: /start",
        "gender": {"male": "Чоловік", "female": "Жінка", "none": "Не вказано"},
        "premium_until": "до {d}", "premium_none": "немає",
        "until": "до {d}",
        "photos": "Фото: {v}", "bio": "Про себе: {v}",
        "card": "<tg-emoji emoji-id=\"5976585328985643006\">🧑‍🧑‍🧒</tg-emoji> Твій профіль\n\n<tg-emoji emoji-id=\"5965485570124681987\">🆔</tg-emoji> ID: {anon}\n<tg-emoji emoji-id=\"{gicon}\">{gemoji}</tg-emoji> Стать: {gender}\n<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Вік: {age}\n<tg-emoji emoji-id=\"5258105663359294787\">📅</tg-emoji> В боті з: {joined}{photos}{bio}\n\n<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Діалогів проведено: {chats}\n<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Лайків: {likes}   <tg-emoji emoji-id=\"5348132683304156113\">👎</tg-emoji> Дизлайків: {dislikes}\n<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> Premium: {premium}",
        "edit_menu": "Що хочеш змінити?",
        "ask_gender": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Обери стать:",
        "ask_age": "🎂 Обери вік:",
        "ask_bio": "<tg-emoji emoji-id=\"5301173701323028420\">✏️</tg-emoji> Кілька слів про себе, до 150 символів. Видно співрозмовнику в профілі. Надішли «-», щоб очистити:",
        "ask_photo": "<tg-emoji emoji-id=\"5258254475386167466\">🖼</tg-emoji> Надішли фото профілю. Воно потрапить до співрозмовника лише після взаємного розкриття.",
        "need_photo": "Надішли фотографію.",
        "need_bio": "Надішли текст або «-», щоб очистити.",
        "bio_too_long": "Задовго — до 150 символів. Спробуй коротше.",
        "cancelled": "Редагування скасовано. Обери пункт меню.",
        "saved": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Збережено",
        "photo_limit": "Досягнуто ліміт фото. Додаткове місце доступне за Stars або з Premium (до 3 фото).",
        "invalid": "Перевір дані. Вік — від 18 до 99. Зміни під час діалогу недоступні.",
        "photo_deleted": "Фото видалено",
        "photo_missing": "Фото не знайдено",
        "delete_ask": "<tg-emoji emoji-id=\"5312229088876847138\">🗑</tg-emoji> Видалити профіль і фотографії? Premium буде вимкнено. Платіжні записи, скарги та службовий аудит зберігаються для обліку й розгляду звернень.",
        "delete_yes": "Так, видалити",
        "delete_no": "Скасувати",
        "delete_stale": "Підтвердження застаріло.",
        "delete_done": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Профіль видалено. Повернутися можна через /start.",
        "profile_missing": "Профіль не знайдено. Відкрий /start.",
        "save_failed": "Не вдалося зберегти налаштування. Відкрий /start.",
        "saved_toast": "Збережено",
        "privacy": "🔒 Приватність\n\nСпіврозмовник не бачить твій Telegram ID, ім'я та username. Username розкривається лише за згоди обох учасників. Фото профілю показується лише за взаємної згоди розкритися.\n\nДані, які ти сам надсилаєш у повідомленнях, видні співрозмовнику. Останні текстові повідомлення тимчасово зберігаються до 30 хвилин для можливої скарги; в базу вони потрапляють лише після явної скарги.\n\nРекомендації та пропозиції: {recs}.\nЦей перемикач керує рекламними й маркетинговими сповіщеннями. Повідомлення про діалоги, оплату та інші службові сповіщення залишаються.",
        "recs_on": "увімкнені", "recs_off": "вимкнені",
        "push_off": "Вимкнути сповіщення", "push_on": "Увімкнути сповіщення",
        "back_profile": "Назад у профіль",
        "rewards": "<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Твої нагороди\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Запрошено: {invited} | Активували: {activated}\nЗапроси друга: після його першого діалогу ти отримаєш годину Premium, друг — знижку 20%.\n{link}\n\n<tg-emoji emoji-id=\"5303040337059543238\">🔥</tg-emoji> Стрік: {streak} днів (UTC)\n3 дні: −10% на разову покупку\n7 днів: −20% на Premium\n30 днів: −30% на Premium 12 місяців\n\n<tg-emoji emoji-id=\"5283130719806175947\">🏆</tg-emoji> Досягнення:\n",
        "event": "Подія: {code}",
        "discounts_note": "Знижки автоматично враховуються в рахунку, без накопичення:",
        "no_discounts": "Поки немає активних знижок.",
        "share": "Поділитися посиланням",
        "achievements": {"first_chat": "Перший діалог", "50_chats": "50 діалогів",
                         "100_chats": "100 діалогів", "streak_7": "7 днів поспіль",
                         "streak_30": "30 днів поспіль", "reveal_1": "Взаємне розкриття",
                         "reveal_5": "5 взаємних розкриттів"},
    },
}


# Telegram wants a custom emoji's fallback to be that emoji's own character,
# so each id travels together with the character it stands for.
GENDER_ICONS = {"male": ("5366121041426922772", "👨‍🦳"),
                "female": ("5366209534933089956", "👵")}
UNKNOWN_GENDER_ICON = ("5348232622898167572", "♥️")


async def show_profile(message, user_id, lang="ru"):
    s = await get_user_stats(user_id)
    words = T.tr(P, lang)
    if not s:
        await message.answer(words["create"])
        return
    gender = words["gender"].get(s['gender']) or words["gender"]["none"]
    gender_icon = GENDER_ICONS.get(s['gender'], UNKNOWN_GENDER_ICON)
    premium = words["premium_until"].format(d=f"{s['premium_expires']:%d.%m.%Y}") if s['is_premium'] else words["premium_none"]
    photos_line = ""
    if s['profile_photo']:
        extra = f" (+{s['extra_photos_count']})" if s['extra_photos_count'] else ""
        photos_line = "\n<tg-emoji emoji-id=\"5258254475386167466\">🖼</tg-emoji> " + words["photos"].format(v=f"1{extra}")
    bio_line = ("\n<tg-emoji emoji-id=\"5301173701323028420\">✏️</tg-emoji> " + words["bio"].format(v=s['bio'])) if s.get('bio') else ""
    text = words["card"].format(anon=s['anon_id'], gicon=gender_icon[0], gemoji=gender_icon[1],
                                gender=gender, age=s['age'], joined=f"{s['created_at']:%d.%m.%Y}",
                                photos=photos_line, bio=bio_line, chats=s['chats_count'],
                                likes=s['likes'], dislikes=s['dislikes'], premium=premium)
    kb = profile_kb(has_photo=bool(s['profile_photo']), lang=lang)
    if s['profile_photo']:
        try:
            await message.answer_photo(s['profile_photo'], caption=text, reply_markup=kb)
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=kb)


@profile_router.message(Command("profile"))
@profile_router.message(F.text.in_(T.all_labels(T.PROFILE)))
async def cmd_profile(message: Message, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await show_profile(message, message.from_user.id, lang)


@profile_router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    await state.clear()
    await callback.answer()
    await show_profile(callback.message, callback.from_user.id, lang)


@profile_router.callback_query(F.data == "profile:edit")
async def cb_edit(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await callback.message.answer(T.tr(P, lang)["edit_menu"], reply_markup=profile_edit_kb(lang))


@profile_router.callback_query(F.data.in_({"profile:set_gender", "profile:set_age", "profile:set_bio", "profile:photo"}))
async def cb_field(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    field = callback.data.split(":")[-1]
    step = {"set_gender": "GENDER", "set_age": "AGE", "set_bio": "BIO", "photo": "PHOTO"}[field]
    await state.set_state(getattr(ProfileEdit, step))
    await state.update_data(age_page=0)
    await callback.answer()
    await callback.message.answer(T.tr(P, lang)["ask_" + step.lower()],
        reply_markup=onboarding_kb(step, lang=lang) if step not in {"PHOTO", "BIO"} else main_menu_kb(lang))


@profile_router.callback_query(F.data == "profile:photo:delete")
async def cb_delete_photo(callback: CallbackQuery, lang: str = "ru"):
    words = T.tr(P, lang)
    result = await ProfileService().remove_photo(callback.from_user.id)
    if result.ok:
        await callback.answer(words["photo_deleted"])
        await show_profile(callback.message, callback.from_user.id, lang)
    else:
        await callback.answer(words["photo_missing"])


@profile_router.message(ProfileEdit.GENDER)
@profile_router.message(ProfileEdit.AGE)
@profile_router.message(ProfileEdit.BIO)
@profile_router.message(ProfileEdit.PHOTO)
async def save_field(message: Message, state: FSMContext, lang: str = "ru"):
    words = T.tr(P, lang)
    step = (await state.get_state()).split(":")[-1]
    text = message.text or ""
    if text in T.SYSTEM_TEXTS - {T.MALE, T.FEMALE, T.PAGE_NEXT, T.PAGE_PREV}:
        await state.clear()
        await message.answer(words["cancelled"], reply_markup=main_menu_kb(lang))
        return
    if step == "PHOTO":
        if not message.photo:
            await message.answer(words["need_photo"])
            return
        result = await ProfileService().add_photo(message.from_user.id, message.photo[-1].file_id)
    elif step == "BIO":
        if not text:
            await message.answer(words["need_bio"])
            return
        value = "" if text.strip() == "-" else text
        if len(value) > 150:
            await message.answer(words["bio_too_long"])
            return
        result = await ProfileService().edit(message.from_user.id, "bio", value)
    elif step == "GENDER":
        value = "male" if text in T.all_labels(T.MALE) else "female" if text in T.all_labels(T.FEMALE) else None
        result = await ProfileService().edit(message.from_user.id, "gender", value)
    else:
        if text in T.all_labels(T.PAGE_NEXT) | T.all_labels(T.PAGE_PREV):
            page = (await state.get_data()).get("age_page", 0)
            page = max(0, min(6, page + (1 if text in T.all_labels(T.PAGE_NEXT) else -1)))
            await state.update_data(age_page=page)
            await message.answer(words["ask_age"], reply_markup=onboarding_kb("AGE", page, lang))
            return
        result = await ProfileService().edit(message.from_user.id, "age", int(text) if text.isascii() and text.isdigit() else None)
    if result.ok:
        await state.clear()
        await message.answer(words["saved"], reply_markup=main_menu_kb(lang))
        await show_profile(message, message.from_user.id, lang)
    elif result.code == "PHOTO_LIMIT":
        from handlers.premium import product_keyboard
        await message.answer(words["photo_limit"], reply_markup=await product_keyboard(["extra_profile_photo", "premium_1m"]))
    else:
        await message.answer(words["invalid"])


@profile_router.callback_query(F.data == "profile:delete")
async def cb_delete(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    words = T.tr(P, lang)
    await state.set_state(ProfileEdit.DELETE_CONFIRM)
    await callback.answer()
    await callback.message.answer(words["delete_ask"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=words["delete_yes"], icon_custom_emoji_id="5312229088876847138", callback_data="profile:delete:confirm", style="danger"),
            InlineKeyboardButton(text=words["delete_no"], callback_data="profile")]]))


@profile_router.callback_query(F.data == "profile:delete:confirm")
async def cb_confirm_delete(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    if await state.get_state() != ProfileEdit.DELETE_CONFIRM.state:
        await callback.answer(T.tr(P, lang)["delete_stale"])
        return
    await ProfileService().delete(callback.from_user.id)
    await state.clear()
    await callback.answer()
    await callback.message.answer(T.tr(P, lang)["delete_done"])


@profile_router.callback_query(F.data.in_({"profile:privacy", "profile:push:on", "profile:push:off"}))
async def cb_privacy(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    from repositories.users import UserRepository
    words = T.tr(P, lang)
    user = await UserRepository().get(callback.from_user.id)
    if not user:
        await callback.answer(words["profile_missing"], show_alert=True)
        return
    enabled = json.loads(user.notification_settings or "{}").get("marketing", True)
    if callback.data != "profile:privacy":
        enabled = callback.data.endswith(":on")
        result = await ProfileService().marketing(callback.from_user.id, enabled)
        if not result.ok:
            await callback.answer(words["save_failed"], show_alert=True)
            return
    await state.clear()
    await callback.answer(words["saved_toast"] if callback.data != "profile:privacy" else None)
    text = words["privacy"].format(recs=words["recs_on"] if enabled else words["recs_off"])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=words["push_off"] if enabled else words["push_on"],
            callback_data="profile:push:off" if enabled else "profile:push:on", style="primary"),
    ], [InlineKeyboardButton(text=words["back_profile"], callback_data="profile")]])
    if callback.data == "profile:privacy":
        await callback.message.answer(text, reply_markup=keyboard)
    else:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message:
                raise


@profile_router.callback_query(F.data == "profile:rewards")
async def cb_rewards(callback: CallbackQuery, lang: str = "ru"):
    from services.growth import GrowthService, StreakService
    from html import escape
    from urllib.parse import quote
    words = T.tr(P, lang)
    streak=await StreakService().checkin(callback.from_user.id)
    user,achievements,offers=await GrowthService().profile(callback.from_user.id)
    bot=await callback.bot.get_me()
    link=f"https://t.me/{bot.username}?start=ref_{user.referral_code}"
    names=words["achievements"]
    text=words["rewards"].format(invited=user.referrals_count, activated=user.referrals_activated, link=link, streak=streak)
    text+="\n".join(("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> " if code in achievements else "<tg-emoji emoji-id=\"5312229088876847138\">🔒</tg-emoji> ")+label for code,label in names.items())
    text+="".join("\n<tg-emoji emoji-id=\"5872889730240089575\">🏅</tg-emoji> "+words["event"].format(code=escape(code.removeprefix('season:'))) for code in achievements if code.startswith("season:"))
    text+="\n\n"+words["discounts_note"]+"\n"
    text+="\n".join(f"−{o.discount_percent}% · {escape(o.product_scope)} · {words['until'].format(d=f'{o.ends_at:%d.%m.%Y %H:%M}')} UTC" for o in offers) or words["no_discounts"]
    await callback.answer()
    await callback.message.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=words["share"], icon_custom_emoji_id="5967432491684860012",url="https://t.me/share/url?url="+quote(link,safe="")),
        InlineKeyboardButton(text="Premium", icon_custom_emoji_id="5920281855378068765",callback_data="premium")]]))
