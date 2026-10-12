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

# Complaint screen. The six reason labels are shown as buttons, so they live
# here too rather than in services.reports -- that module keeps only the keys
# and the Russian label its moderator notice needs.
REPORT_REASONS = {
    "ru": {"18plus": "Контент 18+", "insult": "Оскорбления", "spam": "Спам/реклама",
           "fraud": "Мошенничество", "minor": "Подозрение на несовершеннолетнего", "other": "Другое"},
    "en": {"18plus": "18+ content", "insult": "Insults", "spam": "Spam/ads",
           "fraud": "Fraud", "minor": "Suspected minor", "other": "Other"},
    "uk": {"18plus": "Контент 18+", "insult": "Образи", "spam": "Спам/реклама",
           "fraud": "Шахрайство", "minor": "Підозра на неповнолітнього", "other": "Інше"},
}
REPORT_TEXT = {
    "ru": {"not_in_chat": "Жалоба доступна во время активного диалога.",
           "ask": "<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> На что жалуетесь?",
           "button_unavailable": "Кнопка недоступна.",
           "describe": "Опиши причину жалобы (до 1000 символов). /cancel — отменить.",
           "already": "Жалоба уже отправлена или диалог завершён.",
           "sent": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Жалоба отправлена модераторам.\nСобеседник заблокирован для тебя.",
           "partner_left": "<tg-emoji emoji-id=\"5927208490870248283\">😢</tg-emoji> Собеседник покинул чат.",
           "cancelled": "Ввод жалобы отменён.",
           "describe_range": "Опиши причину текстом от 1 до 1000 символов.",
           "old": "Открой новую жалобу кнопкой «Пожаловаться»."},
    "en": {"not_in_chat": "You can complain only during an active chat.",
           "ask": "<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> What are you complaining about?",
           "button_unavailable": "This button is not available.",
           "describe": "Describe the reason (up to 1000 characters). /cancel — to cancel.",
           "already": "The complaint has already been sent or the chat has ended.",
           "sent": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Complaint sent to the moderators.\nThe peer is blocked for you.",
           "partner_left": "<tg-emoji emoji-id=\"5927208490870248283\">😢</tg-emoji> The peer has left the chat.",
           "cancelled": "Complaint input cancelled.",
           "describe_range": "Describe the reason in text, 1 to 1000 characters.",
           "old": "Open a new complaint with the «Report» button."},
    "uk": {"not_in_chat": "Скарга доступна лише під час активного діалогу.",
           "ask": "<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> На що скаржитесь?",
           "button_unavailable": "Кнопка недоступна.",
           "describe": "Опиши причину скарги (до 1000 символів). /cancel — скасувати.",
           "already": "Скаргу вже надіслано або діалог завершено.",
           "sent": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Скаргу надіслано модераторам.\nСпіврозмовника заблоковано для тебе.",
           "partner_left": "<tg-emoji emoji-id=\"5927208490870248283\">😢</tg-emoji> Співрозмовник покинув чат.",
           "cancelled": "Ввід скарги скасовано.",
           "describe_range": "Опиши причину текстом від 1 до 1000 символів.",
           "old": "Відкрий нову скаргу кнопкою «Поскаржитись»."},
}

# Reveal (exchange contacts) screens and the two keyboards behind them.
REVEAL_BUTTONS = {
    "ru": {"offer": "Предложить раскрыться", "agree": "Согласиться", "decline": "Отказать",
           "priority": "Приоритетный запрос", "status": "Статус"},
    "en": {"offer": "Offer to reveal", "agree": "Agree", "decline": "Decline",
           "priority": "Priority request", "status": "Status"},
    "uk": {"offer": "Запропонувати розкритися", "agree": "Погодитися", "decline": "Відмовити",
           "priority": "Пріоритетний запит", "status": "Статус"},
}
REVEAL_TEXT = {
    "ru": {"not_in_chat": "Ты сейчас не в чате.",
           "intro": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Хочешь продолжить общение открыто?\n\nКонтакт станет виден только после согласия обоих собеседников.",
           "extras": "Дополнительные возможности запроса:",
           "included": "Включено в Premium:",
           "decline_sent": "Отказ отправлен", "request_gone": "Запрос уже недоступен",
           "declined": "Ты отказался раскрывать контакт. Диалог можно продолжить анонимно.",
           "already": "Запрос уже отправлен или диалог завершён.",
           "no_contact": "контакт в Telegram не настроен",
           "mutual": "<tg-emoji emoji-id=\"5994502837327892086\">🎉</tg-emoji> Вы оба хотите общаться открыто!\n\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Собеседник: {contact}",
           "sent": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Запрос отправлен. Ждём согласия собеседника.",
           "need_paid": "Нужен Premium или отдельная покупка для этой функции.",
           "ended": "Диалог уже завершён; контакт не раскрывается.",
           "status_MUTUAL": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Согласие взаимно.",
           "status_PENDING": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Ожидается ответ.",
           "status_DECLINED": "Собеседник отказался раскрывать контакт.",
           "status_NOT_REQUESTED": "Запрос ещё не отправлен.",
           "status_default": "Запрос завершён.",
           "priority_ready": "<tg-emoji emoji-id=\"5350618807943576963\">⚡</tg-emoji> Приоритетный запрос доступен. Подтверди своё согласие:"},
    "en": {"not_in_chat": "You are not in a chat right now.",
           "intro": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Want to continue in the open?\n\nThe contact becomes visible only after both sides agree.",
           "extras": "Extra request options:",
           "included": "Included in Premium:",
           "decline_sent": "Decline sent", "request_gone": "The request is no longer available",
           "declined": "You declined to share your contact. The chat can continue anonymously.",
           "already": "The request has already been sent or the chat has ended.",
           "no_contact": "no Telegram contact set",
           "mutual": "<tg-emoji emoji-id=\"5994502837327892086\">🎉</tg-emoji> You both want to talk in the open!\n\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Your peer: {contact}",
           "sent": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Request sent. Waiting for the peer's consent.",
           "need_paid": "Premium or a separate purchase is required for this feature.",
           "ended": "The chat has already ended; the contact is not revealed.",
           "status_MUTUAL": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Consent is mutual.",
           "status_PENDING": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Waiting for an answer.",
           "status_DECLINED": "The peer declined to share their contact.",
           "status_NOT_REQUESTED": "No request has been sent yet.",
           "status_default": "The request has ended.",
           "priority_ready": "<tg-emoji emoji-id=\"5350618807943576963\">⚡</tg-emoji> Priority request available. Confirm your consent:"},
    "uk": {"not_in_chat": "Ти зараз не в діалозі.",
           "intro": "<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Хочеш продовжити спілкування відкрито?\n\nКонтакт стане видимий лише за згоди обох співрозмовників.",
           "extras": "Додаткові можливості запиту:",
           "included": "Включено в Premium:",
           "decline_sent": "Відмову надіслано", "request_gone": "Запит уже недоступний",
           "declined": "Ти відмовився розкривати контакт. Діалог можна продовжити анонімно.",
           "already": "Запит уже надіслано або діалог завершено.",
           "no_contact": "контакт у Telegram не налаштовано",
           "mutual": "<tg-emoji emoji-id=\"5994502837327892086\">🎉</tg-emoji> Ви обидва хочете спілкуватися відкрито!\n\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Співрозмовник: {contact}",
           "sent": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Запит надіслано. Чекаємо згоди співрозмовника.",
           "need_paid": "Потрібен Premium або окрема покупка для цієї функції.",
           "ended": "Діалог уже завершено; контакт не розкривається.",
           "status_MUTUAL": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Згода взаємна.",
           "status_PENDING": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Очікується відповідь.",
           "status_DECLINED": "Співрозмовник відмовився розкривати контакт.",
           "status_NOT_REQUESTED": "Запит ще не надіслано.",
           "status_default": "Запит завершено.",
           "priority_ready": "<tg-emoji emoji-id=\"5350618807943576963\">⚡</tg-emoji> Пріоритетний запит доступний. Підтверди свою згоду:"},
}

# Notices built outside the dispatcher -- the error trap in bot.py has no
# middleware data, so it resolves the language itself.
ERRORS = {
    "ru": {"retry": "Не удалось выполнить действие. Попробуй ещё раз.",
           "retry_later": "Не удалось выполнить действие. Попробуй ещё раз чуть позже."},
    "en": {"retry": "Something went wrong. Please try again.",
           "retry_later": "Something went wrong. Please try again in a moment."},
    "uk": {"retry": "Не вдалося виконати дію. Спробуй ще раз.",
           "retry_later": "Не вдалося виконати дію. Спробуй ще раз трохи пізніше."},
}

# Buttons and upsell the worker assembles by hand, without a lang argument in
# sight -- they followed the language of whoever wrote them.
WORKER_BUTTONS = {
    "ru": {"similar": "Найти похожего собеседника", "open": "Открыть"},
    "en": {"similar": "Find a similar match", "open": "Open"},
    "uk": {"similar": "Знайти схожого співрозмовника", "open": "Відкрити"},
}
RATING_BUTTONS = {
    "ru": {"yes": "Да", "no": "Нет"},
    "en": {"yes": "Yes", "no": "No"},
    "uk": {"yes": "Так", "no": "Ні"},
}
UPSELL_TEXT = {
    "ru": {"head": "<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> С Premium доступны фильтры, приоритет поиска и «Следующий» без задержки.",
           "spent": "\nЗа последние 30 дней на эти улучшения потрачено {spent}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji>. Premium на месяц стоит {price}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> — на {diff}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> меньше."},
    "en": {"head": "<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> Premium unlocks filters, search priority and instant «Next».",
           "spent": "\nYou spent {spent}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> on these upgrades in the last 30 days. A month of Premium costs {price}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> — that is {diff}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> less."},
    "uk": {"head": "<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> З Premium доступні фільтри, пріоритет пошуку і «Наступний» без затримки.",
           "spent": "\nЗа останні 30 днів на ці покращення витрачено {spent}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji>. Premium на місяць коштує {price}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> — на {diff}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> менше."},
}

LANGUAGE_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська"}
LANGUAGE_FLAGS = {"ru": "🇷🇺", "en": "🇬🇧", "uk": "🇺🇦"}
