from aiogram import F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from handlers import start_router
from keyboards import texts as T
from keyboards.reply import onboarding_kb
from services.onboarding import OnboardingService


class Onboarding(StatesGroup):
    WELCOME = State()
    GENDER = State()
    AGE = State()
    RULES = State()


WELCOME_TEXT = "<tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji> Привет!\n\nЭто анонимный чат-бот <tg-emoji emoji-id=\"5278367493700732437\">🎭</tg-emoji>\n\nЗдесь можно знакомиться и общаться со случайными людьми,\nне раскрывая своё имя, username и Telegram ID.\n\nГотов начать?"
RULES_TEXT = "<tg-emoji emoji-id=\"5976292450870761243\">📜</tg-emoji> Правила общения\n\n1️⃣ Запрещена реклама и спам\n2️⃣ Запрещены оскорбления и травля\n3️⃣ Запрещён запрещённый и нежелательный контент\n4️⃣ Не разглашай личные данные — свои и чужие\n5️⃣ Жалуйся на нарушителей кнопкой <tg-emoji emoji-id=\"5460755126761312667\">🚩</tg-emoji>\n\nДля рассмотрения жалобы последние текстовые сообщения временно хранятся до 30 минут. В базу контекст попадает только после явной отправки жалобы.\n\nНарушение правил может привести к блокировке.\n\nНажимая кнопку ниже, ты подтверждаешь, что ознакомился с правилами. Сервис доступен только с 18 лет."
TEXTS = {"WELCOME": WELCOME_TEXT, "GENDER": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Укажи свой пол\n\nЭто поможет правильно подбирать собеседников.",
         "AGE": "<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Сколько тебе лет?\n\nВыбери свой возраст:", "RULES": RULES_TEXT, "MENU": T.MENU_TEXT}


async def show_step(message, state, result, page=0):
    if result.code == "BANNED":
        until = result.data.get("until")
        await message.answer("⛔ Ты заблокирован " + (f"до {until:%d.%m.%Y %H:%M} UTC." if until else "бессрочно."))
        return
    step = result.code
    if step == "MENU":
        await state.clear()
    else:
        await state.set_state(getattr(Onboarding, step))
        await state.update_data(age_page=page)
    await message.answer(result.data.get("error", TEXTS[step]), reply_markup=onboarding_kb(step, page))


@start_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = message.from_user
    payload = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    result = await OnboardingService().start(user.id, user.username, user.first_name, payload, notify_owner=True)
    await show_step(message, state, result)


@start_router.message(Command("rules"))
async def cmd_rules(message: Message):
    await message.answer(RULES_TEXT)


@start_router.message(StateFilter(None), F.text.in_({T.BEGIN, T.MALE, T.FEMALE, T.ACCEPT, T.RULES}))
@start_router.message(Onboarding.WELCOME)
@start_router.message(Onboarding.GENDER)
@start_router.message(Onboarding.AGE)
@start_router.message(Onboarding.RULES)
async def onboarding_input(message: Message, state: FSMContext):
    text = message.text or ""
    action, value = "invalid", None
    if text == T.BEGIN:
        action = "begin"
    elif text in {T.MALE, T.FEMALE}:
        action, value = "gender", "male" if text == T.MALE else "female"
    elif text == T.ACCEPT:
        action = "accept"
    elif text.isascii() and text.isdigit():
        action, value = "age", int(text)
    result = await OnboardingService().advance(message.from_user.id, action, value)
    page = (await state.get_data()).get("age_page", 0)
    if text in {T.PAGE_NEXT, T.PAGE_PREV} and result.code == "AGE":
        import config
        page = min(max(0, page + (1 if text == T.PAGE_NEXT else -1)), (config.MAX_AGE-config.MIN_AGE)//config.AGE_PAGE_SIZE)
    if text == T.RULES:
        await message.answer(RULES_TEXT)
    await show_step(message, state, result, page)


@start_router.callback_query(F.data.in_({"rules", "rules:ok", "back:start", "menu"}))
async def cb_start_navigation(callback: CallbackQuery, state: FSMContext):
    if callback.data == "rules":
        await callback.message.answer(RULES_TEXT)
    else:
        actor = callback.from_user
        result = await OnboardingService().start(actor.id, actor.username, actor.first_name)
        await show_step(callback.message, state, result)
    await callback.answer()
