from locales import tr

# Canonical action keys -- also the Russian label, kept as-is so existing
# code/tests that compare against these constants for RU output still works.
BEGIN = "Начнём общение"
MALE = "Парень"
FEMALE = "Девушка"
ACCEPT = "Принимаю правила"
RULES = "Читать правила полностью"
SEARCH = "Найти собеседника"
PROFILE = "Мой профиль"
SETTINGS = "Настройки поиска"
PREMIUM = "Premium"
STATS = "Статистика"
HELP = "Помощь"
CHAT = "Наш чат"
INVITE = "Пригласи друга"
MENU = "Меню"
NEXT = "Следующий"
STOP = "Стоп"
REPORT = "Пожаловаться"
REVEAL = "Раскрыть себя"
CANCEL = "Отменить поиск"
PAGE_NEXT = "Дальше"
PAGE_PREV = "Назад"

_ACTIONS = (BEGIN, MALE, FEMALE, ACCEPT, RULES, SEARCH, PROFILE, SETTINGS,
            PREMIUM, STATS, HELP, CHAT, INVITE, MENU, NEXT, STOP, REPORT,
            REVEAL, CANCEL, PAGE_NEXT, PAGE_PREV)

LABELS = {
    BEGIN: {"ru": "Начнём общение", "en": "Let's start", "uk": "Почнімо спілкування"},
    MALE: {"ru": "Парень", "en": "Guy", "uk": "Хлопець"},
    FEMALE: {"ru": "Девушка", "en": "Girl", "uk": "Дівчина"},
    ACCEPT: {"ru": "Принимаю правила", "en": "I accept the rules", "uk": "Приймаю правила"},
    RULES: {"ru": "Читать правила полностью", "en": "Read the full rules", "uk": "Читати правила повністю"},
    SEARCH: {"ru": "Найти собеседника", "en": "Find a match", "uk": "Знайти співрозмовника"},
    PROFILE: {"ru": "Мой профиль", "en": "My profile", "uk": "Мій профіль"},
    SETTINGS: {"ru": "Настройки поиска", "en": "Search settings", "uk": "Налаштування пошуку"},
    PREMIUM: {"ru": "Premium", "en": "Premium", "uk": "Premium"},
    STATS: {"ru": "Статистика", "en": "Stats", "uk": "Статистика"},
    HELP: {"ru": "Помощь", "en": "Help", "uk": "Допомога"},
    CHAT: {"ru": "Наш чат", "en": "Our chat", "uk": "Наш чат"},
    INVITE: {"ru": "Пригласи друга", "en": "Invite a friend", "uk": "Запроси друга"},
    MENU: {"ru": "Меню", "en": "Menu", "uk": "Меню"},
    NEXT: {"ru": "Следующий", "en": "Next", "uk": "Наступний"},
    STOP: {"ru": "Стоп", "en": "Stop", "uk": "Стоп"},
    REPORT: {"ru": "Пожаловаться", "en": "Report", "uk": "Поскаржитись"},
    REVEAL: {"ru": "Раскрыть себя", "en": "Reveal myself", "uk": "Розкрити себе"},
    CANCEL: {"ru": "Отменить поиск", "en": "Cancel search", "uk": "Скасувати пошук"},
    PAGE_NEXT: {"ru": "Дальше", "en": "Next", "uk": "Далі"},
    PAGE_PREV: {"ru": "Назад", "en": "Back", "uk": "Назад"},
}


def label(key, lang):
    return tr(LABELS.get(key, {"ru": key}), lang)


def all_labels(key):
    """Every localized rendering of this action's label -- used in filters so
    a button press is recognized no matter which language it was shown in."""
    return set(LABELS.get(key, {"ru": key}).values())


# Union of every label in every language: used to keep a menu-button press
# from being relayed into a chat as an ordinary message.
SYSTEM_TEXTS = set().union(*(all_labels(action) for action in _ACTIONS))

MENU_TEXT = {
    "ru": "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Главное меню\n\nНайди случайного собеседника и начинай общение <tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji>",
    "en": "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Main menu\n\nFind a random person to talk to <tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji>",
    "uk": "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Головне меню\n\nЗнайди випадкового співрозмовника і починай спілкування <tg-emoji emoji-id=\"5350596259365273418\">👋</tg-emoji>",
}


def menu_text(lang):
    return tr(MENU_TEXT, lang)


EMOJI_IDS = {
    BEGIN: "5224455462178549802",
    MALE: "5366121041426922772",
    FEMALE: "5366209534933089956",
    ACCEPT: "5348404473129614535",
    RULES: "5976791727934020069",
    SEARCH: "5976789958407493804",
    PROFILE: "6032693626394382504",
    SETTINGS: "5348412779596365405",
    PREMIUM: "5258165702707125574",
    STATS: "5884336303415236350",
    HELP: "5805362626619774793",
    MENU: "5257963315258204021",
    NEXT: "5296587908906511469",
    STOP: "5398038979217989221",
    REPORT: "5460755126761312667",
    REVEAL: "5348232622898167572",
    CANCEL: "5325971446625758812",
    PAGE_NEXT: "5260450573768990626",
    PAGE_PREV: "5258236805890710909",
    # The two rows the menu was missing an icon on, so six of eight buttons carried
    # one. Both ids are already Telegram document ids this bot renders elsewhere --
    # the first also sits on a reply button, the second on an inline one -- rather
    # than guessed values, which are what make a keyboard come back DOCUMENT_INVALID.
    CHAT: "5224455462178549802",
    INVITE: "5967432491684860012",
}

# Labels for the inline keyboards that sit behind the main-menu buttons. The menu
# row localizes through LABELS, but these followed it in Russian only, so a client
# running in en/uk tapped a translated menu button and landed on a Russian card.
# Keyed by language first, the way the other screen texts are, so tr() picks a row.
SETTINGS_BUTTONS = {
    "ru": {"m": "Мужчин", "f": "Женщин", "a": "Всех", "any": "Любой"},
    "en": {"m": "Men", "f": "Women", "a": "Everyone", "any": "Any"},
    "uk": {"m": "Чоловіків", "f": "Жінок", "a": "Усіх", "any": "Будь-який"},
}
PROFILE_BUTTONS = {
    "ru": {"fill": "Заполнить о себе", "photo": "Добавить фото", "photo_delete": "Удалить фото",
           "privacy": "Приватность", "rewards": "Приглашения, стрик и награды",
           "delete": "Удалить аккаунт"},
    "en": {"fill": "About you", "photo": "Add photo", "photo_delete": "Delete photo",
           "privacy": "Privacy", "rewards": "Invites, streak and rewards",
           "delete": "Delete account"},
    "uk": {"fill": "Заповнити про себе", "photo": "Додати фото", "photo_delete": "Видалити фото",
           "privacy": "Приватність", "rewards": "Запрошення, стрік і нагороди",
           "delete": "Видалити акаунт"},
}
PROFILE_EDIT_BUTTONS = {
    "ru": {"gender": "Пол", "age": "Возраст", "bio": "О себе", "back": "Назад в профиль"},
    "en": {"gender": "Gender", "age": "Age", "bio": "About", "back": "Back to profile"},
    "uk": {"gender": "Стать", "age": "Вік", "bio": "Про себе", "back": "Назад у профіль"},
}

# What the dispatcher says when a menu tap lands in the wrong moment: mid-dialog,
# mid-registration, blocked, or too fast. These are the first words a user hears
# from the menu itself, so they follow the language like every screen does.
GUARDS = {
    "ru": {"private_only": "Анонимный чат доступен только в личной переписке с ботом.",
           "banned": "⛔ Ты заблокирован.",
           "register_first": "Сначала заверши регистрацию.",
           "finish_chat": "Сначала заверши текущий диалог.",
           "in_chat": "Ты сейчас в диалоге. Нажми «Стоп», чтобы открыть меню.",
           "slow_down": "Подожди немного перед следующим действием."},
    "en": {"private_only": "The anonymous chat works only in a private chat with the bot.",
           "banned": "⛔ You are blocked.",
           "register_first": "Finish the registration first.",
           "finish_chat": "Finish the current chat first.",
           "in_chat": "You are in a chat right now. Tap «Stop» to open the menu.",
           "slow_down": "Wait a moment before the next action."},
    "uk": {"private_only": "Анонімний чат доступний лише в особистому листуванні з ботом.",
           "banned": "⛔ Тебе заблоковано.",
           "register_first": "Спочатку заверши реєстрацію.",
           "finish_chat": "Спочатку заверши поточний діалог.",
           "in_chat": "Ти зараз у діалозі. Натисни «Стоп», щоб відкрити меню.",
           "slow_down": "Зачекай трохи перед наступною дією."},
}

LANGUAGE_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська"}
LANGUAGE_FLAGS = {"ru": "🇷🇺", "en": "🇬🇧", "uk": "🇺🇦"}
