from aiogram import F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from handlers import start_router
from keyboards import texts as T
from keyboards.reply import onboarding_kb, main_menu_kb
from keyboards.inline import language_kb
from services.onboarding import OnboardingService, step_for
from services.result import ServiceResult


class Onboarding(StatesGroup):
    LANGUAGE = State()
    WELCOME = State()
    GENDER = State()
    AGE = State()
    RULES = State()


LANGUAGE_TEXT = "🌐 Choose your language\nВыберите язык\nОберіть мову"

WELCOME_TEXT = {
    "ru": "<tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji> Привет!\n\nЭто анонимный чат-бот <tg-emoji emoji-id=\"5278367493700732437\">🎭</tg-emoji>\n\nПоиск и переписка бесплатны. Собеседник не увидит твой username и Telegram ID, пока вы оба не согласитесь раскрыться.\n\nСервис для людей 18+. Для старта нужны только пол, возраст и принятие правил.\n\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Укажи свой пол, чтобы начать:",
    "en": "<tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji> Hi!\n\nThis is an anonymous chat bot <tg-emoji emoji-id=\"5278367493700732437\">🎭</tg-emoji>\n\nHere you can meet and talk to random people\nwithout revealing your name, username or Telegram ID.\n\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Tell us your gender to start:",
    "uk": "<tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji> Привіт!\n\nЦе анонімний чат-бот <tg-emoji emoji-id=\"5278367493700732437\">🎭</tg-emoji>\n\nТут можна знайомитись і спілкуватися з випадковими людьми,\nне розкриваючи своє ім'я, username і Telegram ID.\n\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Вкажи свою стать, щоб почати:",
}
GENDER_TEXT = {
    "ru": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Укажи свой пол\n\nЭто поможет правильно подбирать собеседников.",
    "en": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Tell us your gender\n\nThis helps match you with the right people.",
    "uk": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Вкажи свою стать\n\nЦе допоможе правильно підбирати співрозмовників.",
}
AGE_TEXT = {
    "ru": "<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Сколько тебе лет?\n\nВыбери свой возраст:",
    "en": "<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> How old are you?\n\nChoose your age:",
    "uk": "<tg-emoji emoji-id=\"5452055425690123301\">🎂</tg-emoji> Скільки тобі років?\n\nОбери свій вік:",
}
AGE_INPUT_HINT = {
    "ru": "Введи возраст кнопкой или цифрами — от 18 до 99.",
    "en": "Pick your age with a button or type digits — 18 to 99.",
    "uk": "Обери вік кнопкою або введи цифри — від 18 до 99.",
}
RULES_TEXT = {
    "ru": "<tg-emoji emoji-id=\"5976292450870761243\">📜</tg-emoji> Правила общения\n\n1️⃣ Запрещена реклама и спам\n2️⃣ Запрещены оскорбления и травля\n3️⃣ Запрещён запрещённый и нежелательный контент\n4️⃣ Не разглашай личные данные — свои и чужие\n5️⃣ Жалуйся на нарушителей кнопкой <tg-emoji emoji-id=\"5460755126761312667\">🚩</tg-emoji>\n\nДля рассмотрения жалобы последние текстовые сообщения временно хранятся до 30 минут. В базу контекст попадает только после явной отправки жалобы.\n\nНарушение правил может привести к блокировке.\n\nНажимая кнопку ниже, ты подтверждаешь, что ознакомился с правилами. Сервис доступен только с 18 лет.",
    "en": "<tg-emoji emoji-id=\"5976292450870761243\">📜</tg-emoji> Chat rules\n\n1️⃣ No ads or spam\n2️⃣ No insults or harassment\n3️⃣ No prohibited or unwanted content\n4️⃣ Don't share personal data — yours or someone else's\n5️⃣ Report violators with the <tg-emoji emoji-id=\"5460755126761312667\">🚩</tg-emoji> button\n\nTo review reports, recent text messages are kept for up to 30 minutes. Nothing is stored unless a report is actually filed.\n\nBreaking the rules can lead to a ban.\n\nBy tapping the button below, you confirm you've read the rules. The service is available from 18 years old only.",
    "uk": "<tg-emoji emoji-id=\"5976292450870761243\">📜</tg-emoji> Правила спілкування\n\n1️⃣ Заборонена реклама і спам\n2️⃣ Заборонені образи й цькування\n3️⃣ Заборонений заборонений і небажаний контент\n4️⃣ Не розголошуй особисті дані — свої й чужі\n5️⃣ Скаржся на порушників кнопкою <tg-emoji emoji-id=\"5460755126761312667\">🚩</tg-emoji>\n\nДля розгляду скарги останні текстові повідомлення тимчасово зберігаються до 30 хвилин. У базу контекст потрапляє лише після явної відправки скарги.\n\nПорушення правил може призвести до блокування.\n\nНатискаючи кнопку нижче, ти підтверджуєш, що ознайомився з правилами. Сервіс доступний лише з 18 років.",
}
BANNED_UNTIL = {"ru": "⛔ Ты заблокирован до {until} UTC.", "en": "⛔ You are banned until {until} UTC.", "uk": "⛔ Тебе заблоковано до {until} UTC."}
BANNED_FOREVER = {"ru": "⛔ Ты заблокирован бессрочно.", "en": "⛔ You are banned permanently.", "uk": "⛔ Тебе заблоковано безстроково."}

TEXTS = {"WELCOME": WELCOME_TEXT, "GENDER": GENDER_TEXT, "AGE": AGE_TEXT, "RULES": RULES_TEXT}


class _DirectTarget:
    """Answers in the user's own chat when the tapped message is too old for
    Telegram to hand back: past ~48 hours CallbackQuery.message comes back
    inaccessible, and answering it raised -- which on the language step looked
    exactly like "registration is broken"."""

    def __init__(self, bot, chat_id):
        self._bot, self._chat_id = bot, chat_id

    async def answer(self, text, reply_markup=None, **kwargs):
        await self._bot.send_message(self._chat_id, text, reply_markup=reply_markup, **kwargs)


def callback_target(callback):
    message = callback.message
    if message is not None and getattr(message, "answer", None) is not None:
        return message
    return _DirectTarget(callback.bot, callback.from_user.id)


async def show_step(message, state, result, page=0, lang="ru"):
    if result.code == "BANNED":
        until = result.data.get("until")
        text = (T.tr(BANNED_UNTIL, lang).format(until=f"{until:%d.%m.%Y %H:%M}") if until
                else T.tr(BANNED_FOREVER, lang))
        await message.answer(text)
        return
    step = result.code
    if step == "LANGUAGE":
        await state.set_state(Onboarding.LANGUAGE)
        await message.answer(LANGUAGE_TEXT, reply_markup=language_kb())
        return
    if step == "MENU":
        await state.clear()
    else:
        await state.set_state(getattr(Onboarding, step))
        await state.update_data(age_page=page)
    error = result.data.get("error") if result.data else None
    text = error or (T.tr(TEXTS[step], lang) if step in TEXTS else T.menu_text(lang))
    await message.answer(text, reply_markup=onboarding_kb(step, page, lang))


@start_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = message.from_user
    payload = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    result = await OnboardingService().start(user.id, user.username, user.first_name, payload, notify_owner=True)
    from utils import bots
    lang = (result.data or {}).get("language") or bots.default_lang(message.bot, user)
    if result.code == "LANGUAGE":
        # Telegram already knows what language its client runs on: a ru/uk/en
        # client lands straight on the welcome screen, anything else still gets
        # the buttons. The picker stays a fallback, not a gate -- every tap it
        # used to demand was one more place to stall, and it stalled 2600 people
        # for a week. Settings can change the language afterwards regardless.
        detected = (getattr(user, "language_code", None) or "").split("-", 1)[0].lower()
        if not getattr(message.bot, "detect_client_lang", True):
            # The second bot is Ukrainian on purpose: it exists for that
            # audience, so it opens in Ukrainian whatever its client runs in.
            detected = bots.default_lang(message.bot)
        if detected in T.LANGUAGE_NAMES:
            result = await OnboardingService().advance(user.id, "lang", detected)
            lang = detected
    await show_step(message, state, result, lang=lang)


@start_router.message(Command("rules"))
async def cmd_rules(message: Message, lang: str = "ru"):
    await message.answer(T.tr(RULES_TEXT, lang))


@start_router.callback_query(F.data.startswith("lang:"))
async def cb_language(callback: CallbackQuery, state: FSMContext):
    code = callback.data.removeprefix("lang:")
    actor = callback.from_user
    target = callback_target(callback)
    from repositories.users import UserRepository
    if code not in T.LANGUAGE_NAMES:
        await callback.answer()
        return
    row = await UserRepository().get(actor.id)
    if row and step_for(row) == "LANGUAGE":
        result = await OnboardingService().advance(actor.id, "lang", code)
        await show_step(target, state, result, lang=code)
    else:
        await OnboardingService().set_language(actor.id, code)
        row = await UserRepository().get(actor.id)
        step = step_for(row) if row else "LANGUAGE"
        if step == "MENU":
            await state.clear()
            await target.answer(
                {"ru": "Язык изменён на Русский.", "en": "Language changed to English.", "uk": "Мову змінено на українську."}[code])
            await target.answer(T.menu_text(code), reply_markup=main_menu_kb(code))
        else:
            # A stale language button tapped mid-registration must not paint a
            # main menu the user has not earned yet -- the flow continues from
            # the step that is actually unfinished, in the new language.
            await show_step(target, state, ServiceResult(True, step), lang=code)
    await callback.answer()


_ONBOARDING_LABELS = T.all_labels(T.BEGIN) | T.all_labels(T.MALE) | T.all_labels(T.FEMALE) | T.all_labels(T.ACCEPT) | T.all_labels(T.RULES)


@start_router.message(StateFilter(None), F.text.in_(_ONBOARDING_LABELS))
@start_router.message(Onboarding.WELCOME)
@start_router.message(Onboarding.GENDER)
@start_router.message(Onboarding.AGE)
@start_router.message(Onboarding.RULES)
async def onboarding_input(message: Message, state: FSMContext, lang: str = "ru"):
    text = message.text or ""
    action, value = "invalid", None
    if text in T.all_labels(T.BEGIN):
        action = "begin"
    elif text in T.all_labels(T.MALE) or text in T.all_labels(T.FEMALE):
        action, value = "gender", "male" if text in T.all_labels(T.MALE) else "female"
    elif text in T.all_labels(T.ACCEPT):
        action = "accept"
    else:
        # Telegram Web and several Android keyboards send a stray space or
        # NBSP with the digit; isdigit() then fails and the tap looks dead.
        digits = text.replace("\u00a0", " ").strip()
        if digits.isascii() and digits.isdigit():
            action, value = "age", int(digits)
    result = await OnboardingService().advance(message.from_user.id, action, value)
    page = (await state.get_data()).get("age_page", 0)
    paging = text in T.all_labels(T.PAGE_NEXT) | T.all_labels(T.PAGE_PREV)
    rules_press = text in T.all_labels(T.RULES)
    if paging and result.code == "AGE":
        import config
        page = min(max(0, page + (1 if text in T.all_labels(T.PAGE_NEXT) else -1)), (config.MAX_AGE-config.MIN_AGE)//config.AGE_PAGE_SIZE)
    elif result.code == "AGE" and action == "invalid" and not rules_press:
        # The age step is the only one the user can type into: silently
        # re-showing the keyboard made the tap look dead.
        result = ServiceResult(False, "AGE", {"error": T.tr(AGE_INPUT_HINT, lang)})
    if rules_press:
        await message.answer(T.tr(RULES_TEXT, lang))
    await show_step(message, state, result, page, lang)


@start_router.callback_query(F.data.in_({"rules", "rules:ok", "back:start", "menu"}))
async def cb_start_navigation(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    target = callback_target(callback)
    if callback.data == "rules":
        await target.answer(T.tr(RULES_TEXT, lang))
    else:
        actor = callback.from_user
        result = await OnboardingService().start(actor.id, actor.username, actor.first_name)
        await show_step(target, state, result, lang=lang)
    await callback.answer()
