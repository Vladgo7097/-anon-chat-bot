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
IDEA = "Предложить идею"
MENU = "Меню"
NEXT = "Следующий"
STOP = "Стоп"
REPORT = "Пожаловаться"
REVEAL = "Раскрыть себя"
CANCEL = "Отменить поиск"
PAGE_NEXT = "Дальше"
PAGE_PREV = "Назад"

_ACTIONS = (
    BEGIN,
    MALE,
    FEMALE,
    ACCEPT,
    RULES,
    SEARCH,
    PROFILE,
    SETTINGS,
    PREMIUM,
    STATS,
    HELP,
    CHAT,
    INVITE,
    IDEA,
    MENU,
    NEXT,
    STOP,
    REPORT,
    REVEAL,
    CANCEL,
    PAGE_NEXT,
    PAGE_PREV,
)

LABELS = {
    BEGIN: {"ru": "Начнём общение", "en": "Let's start", "uk": "Почнімо спілкування"},
    MALE: {"ru": "Парень", "en": "Guy", "uk": "Хлопець"},
    FEMALE: {"ru": "Девушка", "en": "Girl", "uk": "Дівчина"},
    ACCEPT: {
        "ru": "Принимаю правила",
        "en": "I accept the rules",
        "uk": "Приймаю правила",
    },
    RULES: {
        "ru": "Читать правила полностью",
        "en": "Read the full rules",
        "uk": "Читати правила повністю",
    },
    SEARCH: {
        "ru": "Найти собеседника",
        "en": "Find a match",
        "uk": "Знайти співрозмовника",
    },
    PROFILE: {"ru": "Мой профиль", "en": "My profile", "uk": "Мій профіль"},
    SETTINGS: {
        "ru": "Настройки поиска",
        "en": "Search settings",
        "uk": "Налаштування пошуку",
    },
    PREMIUM: {"ru": "Premium", "en": "Premium", "uk": "Premium"},
    STATS: {"ru": "Статистика", "en": "Stats", "uk": "Статистика"},
    HELP: {"ru": "Помощь", "en": "Help", "uk": "Допомога"},
    CHAT: {"ru": "Наш чат", "en": "Our chat", "uk": "Наш чат"},
    INVITE: {"ru": "Пригласи друга", "en": "Invite a friend", "uk": "Запроси друга"},
    IDEA: {
        "ru": "Предложить идею",
        "en": "Suggest an idea",
        "uk": "Запропонувати ідею",
    },
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
    "ru": '<tg-emoji emoji-id="5257963315258204021">🏠</tg-emoji> Главное меню\n\nНайди случайного собеседника и начинай общение <tg-emoji emoji-id="5350596259365273418">👋</tg-emoji>',
    "en": '<tg-emoji emoji-id="5257963315258204021">🏠</tg-emoji> Main menu\n\nFind a random person to talk to <tg-emoji emoji-id="5350596259365273418">👋</tg-emoji>',
    "uk": '<tg-emoji emoji-id="5257963315258204021">🏠</tg-emoji> Головне меню\n\nЗнайди випадкового співрозмовника і починай спілкування <tg-emoji emoji-id="5350596259365273418">👋</tg-emoji>',
}


def menu_text(lang):
    return tr(MENU_TEXT, lang)


EMOJI_IDS = {
    # Правило каталога (dj): новая кнопка стартует БЕЗ иконки, пока у неё
    # нет собственного настоящих doc-id. Заимствовать существующий id нельзя:
    # это красит новое место иконкой старого экрана, а угаданный id роняет
    # клавиатуру целиком (DOCUMENT_INVALID). Пустая строка = кнопка без иконки.
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
    # Идея НЕ несёт иконки: dj убрал заимствование существующих id.
    # Новые кнопки стартуют без иконки, пока у них свой настоящий doc-id --
    # чужой кастомный id вешать нельзя (визуальный клон старого экрана и
    # риск DOCUMENT_INVALID на всей клавиатуре).
    IDEA: "",
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
    "ru": {
        "fill": "Заполнить о себе",
        "photo": "Добавить фото",
        "photo_delete": "Удалить фото",
        "privacy": "Приватность",
        "rewards": "Приглашения, стрик и награды",
        "friends": "Друзья",
        "delete": "Удалить аккаунт",
    },
    "en": {
        "fill": "About you",
        "photo": "Add photo",
        "photo_delete": "Delete photo",
        "privacy": "Privacy",
        "rewards": "Invites, streak and rewards",
        "friends": "Friends",
        "delete": "Delete account",
    },
    "uk": {
        "fill": "Заповнити про себе",
        "photo": "Додати фото",
        "photo_delete": "Видалити фото",
        "privacy": "Приватність",
        "rewards": "Запрошення, стрік і нагороди",
        "friends": "Друзі",
        "delete": "Видалити акаунт",
    },
}

# Everything that appears after a voluntary chat end or in the friends flow.
# The labels live here so the worker, the after-chat card and the handlers all
# read the same three-language row.
CHAT_AFTER = {
    "ru": {
        "text": "Диалог завершён. Вернуть собеседника можно в течение 10 минут.",
        "reconnect": "Вернуть собеседника",
        "friend": "Добавить в друзья",
        "menu": "Меню",
    },
    "en": {
        "text": "The chat has ended. You can bring the same partner back for 10 minutes.",
        "reconnect": "Bring the partner back",
        "friend": "Add as a friend",
        "menu": "Menu",
    },
    "uk": {
        "text": "Діалог завершено. Повернути співрозмовника можна протягом 10 хвилин.",
        "reconnect": "Повернути співрозмовника",
        "friend": "Додати в друзі",
        "menu": "Меню",
    },
}

COMMUNITY_BUTTONS = {
    "ru": {
        "accept": "Принять",
        "decline": "Отклонить",
        "open": "Открыть",
        "invite": "Позвать в чат",
        "back": "Назад в профиль",
    },
    "en": {
        "accept": "Accept",
        "decline": "Decline",
        "open": "Open",
        "invite": "Invite to chat",
        "back": "Back to profile",
    },
    "uk": {
        "accept": "Прийняти",
        "decline": "Відхилити",
        "open": "Відкрити",
        "invite": "Покликати в чат",
        "back": "Назад у профіль",
    },
}

FRIEND_TEXT = {
    "ru": {
        "request_sent": "Запрос дружбы отправлен.",
        "already": "Заявка уже отправлена.",
        "friends": "Вы уже друзья.",
        "blocked": "Этот пользователь недоступен.",
        "cooldown": "Повторить можно позже.",
        "limit": "Достигнут лимит друзей или заявок.",
        "not_found": "Заявка уже недоступна.",
        "accepted": "Теперь вы друзья.",
        "declined": "Заявка отклонена.",
        "accepted_toast": "Принято",
        "declined_toast": "Отклонено",
        "title": "👥 Друзья",
        "empty": "Пока никого. Добавляй собеседников после диалога.",
        "row": "• {anon}",
        "invite_sent": "Приглашение отправлено.",
        "invite_cooldown": "Приглашение уже отправлено, подожди.",
        "busy": "Собеседник сейчас в другом диалоге.",
        "joined": "Открываю чат.",
        "not_friend": "Это не друг.",
        "reconnect_waiting": "Ждём собеседника. Если он нажмёт в течение 10 минут, вы снова в чате.",
        "reconnect_done": "Вы снова в чате.",
        "reconnect_expired": "Время на возврат вышло.",
        "reconnect_blocked": "Возврат недоступен.",
        "reconnect_busy": "Кто-то из вас уже в другом диалоге.",
        "reconnect_already": "Возврат уже использован.",
    },
    "en": {
        "request_sent": "Friend request sent.",
        "already": "The request has already been sent.",
        "friends": "You are already friends.",
        "blocked": "This user is unavailable.",
        "cooldown": "You can try again later.",
        "limit": "The friend or request limit has been reached.",
        "not_found": "The request is no longer available.",
        "accepted": "You are friends now.",
        "declined": "The request was declined.",
        "accepted_toast": "Accepted",
        "declined_toast": "Declined",
        "title": "👥 Friends",
        "empty": "Nobody yet. Add people after a chat.",
        "row": "• {anon}",
        "invite_sent": "Invitation sent.",
        "invite_cooldown": "An invitation was already sent, wait a bit.",
        "busy": "Your friend is in another chat right now.",
        "joined": "Opening the chat.",
        "not_friend": "This is not a friend.",
        "reconnect_waiting": "Waiting for your partner. If they tap within 10 minutes, you are back in the chat.",
        "reconnect_done": "You are back in the chat.",
        "reconnect_expired": "The return window has closed.",
        "reconnect_blocked": "Return is unavailable.",
        "reconnect_busy": "One of you is already in another chat.",
        "reconnect_already": "This return has already been used.",
    },
    "uk": {
        "request_sent": "Запит на дружбу надіслано.",
        "already": "Заявку вже надіслано.",
        "friends": "Ви вже друзі.",
        "blocked": "Цей користувач недоступний.",
        "cooldown": "Спробувати можна пізніше.",
        "limit": "Досягнуто ліміт друзів або заявок.",
        "not_found": "Заявка вже недоступна.",
        "accepted": "Тепер ви друзі.",
        "declined": "Заявку відхилено.",
        "accepted_toast": "Прийнято",
        "declined_toast": "Відхилено",
        "title": "👥 Друзі",
        "empty": "Поки нікого. Додавай співрозмовників після діалогу.",
        "row": "• {anon}",
        "invite_sent": "Запрошення надіслано.",
        "invite_cooldown": "Запрошення вже надіслано, зачекай.",
        "busy": "Співрозмовник зараз в іншому діалозі.",
        "joined": "Відкриваю чат.",
        "not_friend": "Це не друг.",
        "reconnect_waiting": "Чекаємо на співрозмовника. Якщо він натисне протягом 10 хвилин, ви знову в чаті.",
        "reconnect_done": "Ви знову в чаті.",
        "reconnect_expired": "Час на повернення минув.",
        "reconnect_blocked": "Повернення недоступне.",
        "reconnect_busy": "Хтось із вас уже в іншому діалозі.",
        "reconnect_already": "Повернення вже використано.",
    },
}

FRIEND_NOTIFY = {
    "ru": {
        "request": "👋 {anon} хочет добавить тебя в друзья.",
        "accepted": "✅ {anon} принял заявку в друзья.",
        "invite": "💬 {anon} зовёт тебя в чат.",
        "reconnect": "↩️ {anon} хочет вернуть ваш диалог. Нажми, пока не прошло 10 минут.",
    },
    "en": {
        "request": "👋 {anon} wants to add you as a friend.",
        "accepted": "✅ {anon} accepted your friend request.",
        "invite": "💬 {anon} invites you to a chat.",
        "reconnect": "↩️ {anon} wants to bring your chat back. Tap before the 10 minutes are up.",
    },
    "uk": {
        "request": "👋 {anon} хоче додати тебе в друзі.",
        "accepted": "✅ {anon} прийняв заявку в друзі.",
        "invite": "💬 {anon} кличе тебе в чат.",
        "reconnect": "↩️ {anon} хоче повернути ваш діалог. Натисни, поки не минуло 10 хвилин.",
    },
}
PROFILE_EDIT_BUTTONS = {
    "ru": {
        "gender": "Пол",
        "age": "Возраст",
        "bio": "О себе",
        "back": "Назад в профиль",
    },
    "en": {"gender": "Gender", "age": "Age", "bio": "About", "back": "Back to profile"},
    "uk": {
        "gender": "Стать",
        "age": "Вік",
        "bio": "Про себе",
        "back": "Назад у профіль",
    },
}

# What the dispatcher says when a menu tap lands in the wrong moment: mid-dialog,
# mid-registration, blocked, or too fast. These are the first words a user hears
# from the menu itself, so they follow the language like every screen does.
GUARDS = {
    "ru": {
        "banned": "⛔ Ты заблокирован.",
        "register_first": "Сначала заверши регистрацию.",
        "finish_chat": "Сначала заверши текущий диалог.",
        "in_chat": "Ты сейчас в диалоге. Нажми «Стоп», чтобы открыть меню.",
        "slow_down": "Подожди немного перед следующим действием.",
    },
    "en": {
        "banned": "⛔ You are blocked.",
        "register_first": "Finish the registration first.",
        "finish_chat": "Finish the current chat first.",
        "in_chat": "You are in a chat right now. Tap «Stop» to open the menu.",
        "slow_down": "Wait a moment before the next action.",
    },
    "uk": {
        "banned": "⛔ Тебе заблоковано.",
        "register_first": "Спочатку заверши реєстрацію.",
        "finish_chat": "Спочатку заверши поточний діалог.",
        "in_chat": "Ти зараз у діалозі. Натисни «Стоп», щоб відкрити меню.",
        "slow_down": "Зачекай трохи перед наступною дією.",
    },
}

# Complaint screen. The six reason labels are shown as buttons, so they live
# here too rather than in services.reports -- that module keeps only the keys
# and the Russian label its moderator notice needs.
REPORT_REASONS = {
    "ru": {
        "18plus": "Контент 18+",
        "insult": "Оскорбления",
        "spam": "Спам/реклама",
        "fraud": "Мошенничество",
        "minor": "Подозрение на несовершеннолетнего",
        "other": "Другое",
    },
    "en": {
        "18plus": "18+ content",
        "insult": "Insults",
        "spam": "Spam/ads",
        "fraud": "Fraud",
        "minor": "Suspected minor",
        "other": "Other",
    },
    "uk": {
        "18plus": "Контент 18+",
        "insult": "Образи",
        "spam": "Спам/реклама",
        "fraud": "Шахрайство",
        "minor": "Підозра на неповнолітнього",
        "other": "Інше",
    },
}
REPORT_TEXT = {
    "ru": {
        "not_in_chat": "Жалоба доступна во время активного диалога.",
        "ask": '<tg-emoji emoji-id="5280880410346152584">🚩</tg-emoji> На что жалуетесь?',
        "button_unavailable": "Кнопка недоступна.",
        "describe": "Опиши причину жалобы (до 1000 символов). /cancel — отменить.",
        "already": "Жалоба уже отправлена или диалог завершён.",
        "sent": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Жалоба отправлена модераторам.\nСобеседник заблокирован для тебя.',
        "partner_left": '<tg-emoji emoji-id="5927208490870248283">😢</tg-emoji> Собеседник покинул чат.',
        "cancelled": "Ввод жалобы отменён.",
        "describe_range": "Опиши причину текстом от 1 до 1000 символов.",
        "old": "Открой новую жалобу кнопкой «Пожаловаться».",
    },
    "en": {
        "not_in_chat": "You can complain only during an active chat.",
        "ask": '<tg-emoji emoji-id="5280880410346152584">🚩</tg-emoji> What are you complaining about?',
        "button_unavailable": "This button is not available.",
        "describe": "Describe the reason (up to 1000 characters). /cancel — to cancel.",
        "already": "The complaint has already been sent or the chat has ended.",
        "sent": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Complaint sent to the moderators.\nThe peer is blocked for you.',
        "partner_left": '<tg-emoji emoji-id="5927208490870248283">😢</tg-emoji> The peer has left the chat.',
        "cancelled": "Complaint input cancelled.",
        "describe_range": "Describe the reason in text, 1 to 1000 characters.",
        "old": "Open a new complaint with the «Report» button.",
    },
    "uk": {
        "not_in_chat": "Скарга доступна лише під час активного діалогу.",
        "ask": '<tg-emoji emoji-id="5280880410346152584">🚩</tg-emoji> На що скаржитесь?',
        "button_unavailable": "Кнопка недоступна.",
        "describe": "Опиши причину скарги (до 1000 символів). /cancel — скасувати.",
        "already": "Скаргу вже надіслано або діалог завершено.",
        "sent": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Скаргу надіслано модераторам.\nСпіврозмовника заблоковано для тебе.',
        "partner_left": '<tg-emoji emoji-id="5927208490870248283">😢</tg-emoji> Співрозмовник покинув чат.',
        "cancelled": "Ввід скарги скасовано.",
        "describe_range": "Опиши причину текстом від 1 до 1000 символів.",
        "old": "Відкрий нову скаргу кнопкою «Поскаржитись».",
    },
}

# Reveal (exchange contacts) screens and the two keyboards behind them.
REVEAL_BUTTONS = {
    "ru": {
        "offer": "Предложить раскрыться",
        "agree": "Согласиться",
        "decline": "Отказать",
        "priority": "Приоритетный запрос",
        "status": "Статус",
    },
    "en": {
        "offer": "Offer to reveal",
        "agree": "Agree",
        "decline": "Decline",
        "priority": "Priority request",
        "status": "Status",
    },
    "uk": {
        "offer": "Запропонувати розкритися",
        "agree": "Погодитися",
        "decline": "Відмовити",
        "priority": "Пріоритетний запит",
        "status": "Статус",
    },
}
REVEAL_TEXT = {
    "ru": {
        "not_in_chat": "Ты сейчас не в чате.",
        "intro": '<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Хочешь продолжить общение открыто?\n\nКонтакт станет виден только после согласия обоих собеседников.',
        "extras": "Дополнительные возможности запроса:",
        "included": "Включено в Premium:",
        "decline_sent": "Отказ отправлен",
        "request_gone": "Запрос уже недоступен",
        "declined": "Ты отказался раскрывать контакт. Диалог можно продолжить анонимно.",
        "already": "Запрос уже отправлен или диалог завершён.",
        "no_contact": "контакт в Telegram не настроен",
        "mutual": '<tg-emoji emoji-id="5994502837327892086">🎉</tg-emoji> Вы оба хотите общаться открыто!\n\n<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Собеседник: {contact}',
        "sent": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Запрос отправлен. Ждём согласия собеседника.',
        "need_paid": "Нужен Premium или отдельная покупка для этой функции.",
        "ended": "Диалог уже завершён; контакт не раскрывается.",
        "status_MUTUAL": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Согласие взаимно.',
        "status_PENDING": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Ожидается ответ.',
        "status_DECLINED": "Собеседник отказался раскрывать контакт.",
        "status_NOT_REQUESTED": "Запрос ещё не отправлен.",
        "status_default": "Запрос завершён.",
        "priority_ready": '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Приоритетный запрос доступен. Подтверди своё согласие:',
        "request_push": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Собеседник предложил взаимно раскрыть контакты.',
        "decline_push": "Собеседник отказался раскрывать контакт.",
    },
    "en": {
        "not_in_chat": "You are not in a chat right now.",
        "intro": '<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Want to continue in the open?\n\nThe contact becomes visible only after both sides agree.',
        "extras": "Extra request options:",
        "included": "Included in Premium:",
        "decline_sent": "Decline sent",
        "request_gone": "The request is no longer available",
        "declined": "You declined to share your contact. The chat can continue anonymously.",
        "already": "The request has already been sent or the chat has ended.",
        "no_contact": "no Telegram contact set",
        "mutual": '<tg-emoji emoji-id="5994502837327892086">🎉</tg-emoji> You both want to talk in the open!\n\n<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Your peer: {contact}',
        "sent": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Request sent. Waiting for the peer\'s consent.',
        "need_paid": "Premium or a separate purchase is required for this feature.",
        "ended": "The chat has already ended; the contact is not revealed.",
        "status_MUTUAL": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Consent is mutual.',
        "status_PENDING": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Waiting for an answer.',
        "status_DECLINED": "The peer declined to share their contact.",
        "status_NOT_REQUESTED": "No request has been sent yet.",
        "status_default": "The request has ended.",
        "priority_ready": '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Priority request available. Confirm your consent:',
        "request_push": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Your match suggests mutually revealing contacts.',
        "decline_push": "Your match declined to share contacts.",
    },
    "uk": {
        "not_in_chat": "Ти зараз не в діалозі.",
        "intro": '<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Хочеш продовжити спілкування відкрито?\n\nКонтакт стане видимий лише за згоди обох співрозмовників.',
        "extras": "Додаткові можливості запиту:",
        "included": "Включено в Premium:",
        "decline_sent": "Відмову надіслано",
        "request_gone": "Запит уже недоступний",
        "declined": "Ти відмовився розкривати контакт. Діалог можна продовжити анонімно.",
        "already": "Запит уже надіслано або діалог завершено.",
        "no_contact": "контакт у Telegram не налаштовано",
        "mutual": '<tg-emoji emoji-id="5994502837327892086">🎉</tg-emoji> Ви обидва хочете спілкуватися відкрито!\n\n<tg-emoji emoji-id="5348232622898167572">👤</tg-emoji> Співрозмовник: {contact}',
        "sent": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Запит надіслано. Чекаємо згоди співрозмовника.',
        "need_paid": "Потрібен Premium або окрема покупка для цієї функції.",
        "ended": "Діалог уже завершено; контакт не розкривається.",
        "status_MUTUAL": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Згода взаємна.',
        "status_PENDING": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Очікується відповідь.',
        "status_DECLINED": "Співрозмовник відмовився розкривати контакт.",
        "status_NOT_REQUESTED": "Запит ще не надіслано.",
        "status_default": "Запит завершено.",
        "priority_ready": '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Пріоритетний запит доступний. Підтверди свою згоду:',
        "request_push": '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Співрозмовник пропонує взаємно розкрити контакти.',
        "decline_push": "Співрозмовник відмовився розкривати контакт.",
    },
}

# Notices built outside the dispatcher -- the error trap in bot.py has no
# middleware data, so it resolves the language itself.
ERRORS = {
    "ru": {
        "retry": "Не удалось выполнить действие. Попробуй ещё раз.",
        "retry_later": "Не удалось выполнить действие. Попробуй ещё раз чуть позже.",
    },
    "en": {
        "retry": "Something went wrong. Please try again.",
        "retry_later": "Something went wrong. Please try again in a moment.",
    },
    "uk": {
        "retry": "Не вдалося виконати дію. Спробуй ще раз.",
        "retry_later": "Не вдалося виконати дію. Спробуй ще раз трохи пізніше.",
    },
}

# Buttons and upsell the worker assembles by hand, without a lang argument in
# sight -- they followed the language of whoever wrote them.
WORKER_BUTTONS = {
    "ru": {"similar": "Найти похожего собеседника", "open": "Открыть"},
    "en": {"similar": "Find a similar match", "open": "Open"},
    "uk": {"similar": "Знайти схожого співрозмовника", "open": "Відкрити"},
}
# Тексты, замораживаемые в outbox при постановке (worker не переводит их при
# доставке — он только прикладывает клавиатуру). Локализация обязана случиться
# здесь, в момент enqueue, по языку получателя.
WORKER_TEXTS = {
    "ru": {
        "ended_timeout": "Диалог завершён по неактивности.",
        "ended_new": "Диалог завершён. Можно начать новый поиск.",
        "reconnect_prompt": "Найти нового собеседника с теми же настройками?",
        "rating_prompt": "Нашли интересного собеседника?",
        "rated_up": '<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> Тебя оценили после последнего диалога!',
        "rated_down": '<tg-emoji emoji-id="5348132683304156113">👎</tg-emoji> Получена оценка после диалога.',
    },
    "en": {
        "ended_timeout": "The chat ended due to inactivity.",
        "ended_new": "The chat ended. You can start a new search.",
        "reconnect_prompt": "Find a new match with the same settings?",
        "rating_prompt": "Did you enjoy your chat?",
        "rated_up": '<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> You got a thumbs up after your last chat!',
        "rated_down": '<tg-emoji emoji-id="5348132683304156113">👎</tg-emoji> You got a thumbs down after your last chat.',
    },
    "uk": {
        "ended_timeout": "Діалог завершено через неактивність.",
        "ended_new": "Діалог завершено. Можна почати новий пошук.",
        "reconnect_prompt": "Знайти нового співрозмовника з тими ж налаштуваннями?",
        "rating_prompt": "Знайшли цікавого співрозмовника?",
        "rated_up": '<tg-emoji emoji-id="5350417283783084711">👍</tg-emoji> Вас оцінили після останнього діалогу!',
        "rated_down": '<tg-emoji emoji-id="5348132683304156113">👎</tg-emoji> Отримано оцінку після діалогу.',
    },
}
RATING_BUTTONS = {
    "ru": {"yes": "Да", "no": "Нет"},
    "en": {"yes": "Yes", "no": "No"},
    "uk": {"yes": "Так", "no": "Ні"},
}
UPSELL_TEXT = {
    "ru": {
        "head": '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> С Premium доступны фильтры, приоритет поиска и «Следующий» без задержки.',
        "spent": '\nЗа последние 30 дней на эти улучшения потрачено {spent}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji>. Premium на месяц стоит {price}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> — на {diff}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> меньше.',
    },
    "en": {
        "head": '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium unlocks filters, search priority and instant «Next».',
        "spent": '\nYou spent {spent}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> on these upgrades in the last 30 days. A month of Premium costs {price}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> — that is {diff}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> less.',
    },
    "uk": {
        "head": '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> З Premium доступні фільтри, пріоритет пошуку і «Наступний» без затримки.',
        "spent": '\nЗа останні 30 днів на ці покращення витрачено {spent}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji>. Premium на місяць коштує {price}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> — на {diff}<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> менше.',
    },
}

LANGUAGE_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська"}
LANGUAGE_FLAGS = {"ru": "🇷🇺", "en": "🇬🇧", "uk": "🇺🇦"}

# ── Идеи («Предложить идею») ─────────────────────────────────────
# Пользовательские экраны: HTML-разметка, пользовательский текст в них
# вставляется только через html.escape в handler'е.
IDEA_MENU = {
    "ru": '<tg-emoji emoji-id="5345843457145453967">💡</tg-emoji> <b>Предложи идею для улучшения бота!</b>\n\nЕсть идея новой функции, полезное улучшение или предложение, которое сделает бота удобнее?\n\nРасскажи, что ты хотел бы добавить или изменить. Все предложения будут сохранены и переданы на рассмотрение владельцу.\n\nВыбирай действие ниже 👇',
    "en": '<tg-emoji emoji-id="5345843457145453967">💡</tg-emoji> <b>Suggest an idea to improve the bot!</b>\n\nGot a feature idea, a useful improvement or a suggestion that would make the bot handier?\n\nTell us what you would add or change. Every suggestion is saved and passed to the owner for review.\n\nPick an action below 👇',
    "uk": '<tg-emoji emoji-id="5345843457145453967">💡</tg-emoji> <b>Запропонуй ідею для покращення бота!</b>\n\nЄ ідея нової функції, корисне удосконалення чи пропозиція, яка зробить бота зручнішим?\n\nРозкажи, що ти хотів би додати чи змінити. Усі пропозиції збережуться і передадуться власнику на розгляд.\n\nВибирай дію нижче 👇',
}
IDEA_ASK = {
    "ru": "Напиши свою идею одним сообщением.\n\nРасскажи, какую функцию ты хочешь видеть в боте, как она должна работать и какую пользу принесёт.\n\nПостарайся описать идею конкретно. Например: добавить возможность выбирать несколько параметров поиска собеседника или создать систему пользовательских достижений.",
    "en": "Write your idea in one message.\n\nWhat feature do you want in the bot, how should it work and what good would it do?\n\nTry to be concrete. For example: multiple search filters, or a user achievements system.",
    "uk": "Напиши свою ідею одним повідомленням.\n\nРозкажи, яку функцію ти хочеш бачити в боті, як вона має працювати і яку користь принесе.\n\nПостарайся описати ідею конкретно. Наприклад: можливість вибирати кілька параметрів пошуку співрозмовника чи систему користувацьких досягнень.",
}
IDEA_EDIT_ASK = {
    "ru": "Пришли новый текст идеи — вместо прежнего.",
    "en": "Send the new wording — it replaces the old one.",
    "uk": "Надішли новий текст ідеї — замість попереднього.",
}
IDEA_PREVIEW = {
    "ru": "📝 <b>Проверь своё предложение</b>\n\n<b>Тема:</b> Новая функция для бота\n\n<b>Описание:</b>\n{text}\n\nВсё правильно? После подтверждения идея будет передана владельцу на рассмотрение",
    "en": "📝 <b>Check your suggestion</b>\n\n<b>Topic:</b> A new feature for the bot\n\n<b>Description:</b>\n{text}\n\nAll correct? Once confirmed, the idea goes to the owner for review",
    "uk": "📝 <b>Перевір свою пропозицію</b>\n\n<b>Тема:</b> Нова функція для бота\n\n<b>Опис:</b>\n{text}\n\nВсе правильно? Після підтвердження ідея передасться власнику на розгляд",
}
IDEA_SUCCESS = {
    "ru": "✅ <b>Идея успешно отправлена!</b>\n\nСпасибо за предложение. Оно передано на рассмотрение владельцу бота.\n\nНомер твоей идеи: <b>#{id}</b>\n\nТы сможешь посмотреть её статус в разделе «Мои предложения».\n\nСпасибо за помощь в развитии бота! 💙",
    "en": "✅ <b>Idea sent!</b>\n\nThanks for the suggestion. It has been passed to the bot owner for review.\n\nYour idea number: <b>#{id}</b>\n\nYou can follow its status under “My suggestions”.\n\nThanks for helping the bot grow! 💙",
    "uk": "✅ <b>Ідею успішно надіслано!</b>\n\nДякуємо за пропозицію. Вона передана на розгляд власнику бота.\n\nНомер твоєї ідеї: <b>#{id}</b>\n\nСтатус можна переглянути в розділі «Мої пропозиції».\n\nДякуємо за допомогу в розвитку бота! 💙",
}
IDEA_CANCELLED = {
    "ru": "Черновик отменён, ничего не отправлено. Можно начать с чистого листа.",
    "en": "Draft discarded, nothing was sent. A clean sheet whenever you're ready.",
    "uk": "Чернетку скасовано, нічого не надіслано. Можна починати з чистого аркуша.",
}
IDEA_NOT_TEXT = {
    "ru": "Файлы и стикеры не подходят — пришли идею обычным текстом одним сообщением.",
    "en": "Files and stickers won't work — send the idea as a plain text message.",
    "uk": "Файли та стикери не підходять — надішли ідею звичайним текстом одним повідомленням.",
}
IDEA_EMPTY = {
    "ru": "Сообщение пустое. Напиши суть идеи текстом.",
    "en": "The message is empty. Write the idea in plain text.",
    "uk": "Повідомлення порожнє. Напиши суть ідеї текстом.",
}
IDEA_TOO_LONG = {
    "ru": "Слишком длинно: {len} из {max} символов. Сократи идею до {max} символов — короткое предложение читают и рассматривают быстрее.",
    "en": "Too long: {len} of {max} characters. Trim the idea to {max} — a short suggestion gets read and reviewed sooner.",
    "uk": "Задовго: {len} із {max} символів. Скороти ідею до {max} символів — коротку пропозицію читають швидше.",
}
IDEA_LIMIT_DAY = {
    "ru": "Лимит: не больше {max} идей в сутки на человека. Место освободится около {when} (Киев).",
    "en": "Limit: at most {max} ideas per day per person. A slot frees up around {when} (Kyiv).",
    "uk": "Ліміт: не більше {max} ідей на добу на людину. Місце звільниться біля {when} (Київ).",
}
IDEA_COOLDOWN = {
    "ru": "Не спеши: следующую идею можно отправить через {min} мин.",
    "en": "Not so fast: the next idea can go in {min} min.",
    "uk": "Не поспішай: наступну ідею можна надіслати через {min} хв.",
}
IDEA_CALL_DONE = {
    "ru": "⏱ Черновика больше нет: заявление уже обработано, ничего нового не создано.",
    "en": "⏱ The draft is gone: it was already handled and nothing new was created.",
    "uk": "⏱ Чернетки більше немає: заявку вже оброблено, нічого нового не створено.",
}
IDEA_DUP_TEXT = {
    "ru": "Такую идею ты уже отправил за последние сутки — она в очереди на рассмотрение.",
    "en": "You already sent this idea within the last 24 hours - it is awaiting review.",
    "uk": "Таку ідею ти вже надсилав за останню добу — вона в черзі на розгляд.",
}
IDEA_MINE_HEADER = {
    "ru": "📋 <b>Твои предложения</b>\n\n{list}\n\nВыбери предложение, чтобы посмотреть подробности.",
    "en": "📋 <b>Your suggestions</b>\n\n{list}\n\nTap an entry to see the details.",
    "uk": "📋 <b>Твої пропозиції</b>\n\n{list}\n\nВибери пропозицію, щоб побачити подробиці.",
}
IDEA_MINE_EMPTY = {
    "ru": "У тебя пока нет предложений. Будь первым — поделись идеей!",
    "en": "You have no suggestions yet. Be the first — share an idea!",
    "uk": "У тебе ще немає пропозицій. Будь першим — поділись ідеєю!",
}
IDEA_CARD = {
    "ru": "💡 <b>Твоя идея #{id}</b>\n\n{status}\n\n<b>Текст:</b>\n{text}\n\n📅 Отправлена: {created}\n🕒 Обновлена: {updated}{reply}",
    "en": "💡 <b>Your idea #{id}</b>\n\n{status}\n\n<b>Text:</b>\n{text}\n\n📅 Sent: {created}\n🕒 Updated: {updated}{reply}",
    "uk": "💡 <b>Твоя ідея #{id}</b>\n\n{status}\n\n<b>Текст:</b>\n{text}\n\n📅 Надіслано: {created}\n🕒 Оновлено: {updated}{reply}",
}
IDEA_CARD_REPLY = {
    "ru": "\n\n💬 <b>Ответ владельца</b> ({date}):\n{reply}",
    "en": "\n\n💬 <b>Owner's reply</b> ({date}):\n{reply}",
    "uk": "\n\n💬 <b>Відповідь власника</b> ({date}):\n{reply}",
}
IDEA_STATUSES = {
    "new": {"ru": "🆕 Новая", "en": "🆕 New", "uk": "🆕 Нова"},
    "review": {
        "ru": "🟡 На рассмотрении",
        "en": "🟡 In review",
        "uk": "🟡 На розгляді",
    },
    "planned": {"ru": "🔵 Запланирована", "en": "🔵 Planned", "uk": "🔵 Запланована"},
    "in_progress": {
        "ru": "🛠 В разработке",
        "en": "🛠 In progress",
        "uk": "🛠 У розробці",
    },
    "implemented": {
        "ru": "✅ Реализована",
        "en": "✅ Implemented",
        "uk": "✅ Реалізована",
    },
    "rejected": {"ru": "❌ Отклонена", "en": "❌ Rejected", "uk": "❌ Відхилена"},
    "duplicate": {"ru": "🔁 Дубликат", "en": "🔁 Duplicate", "uk": "🔁 Дублікат"},
}
# Статус отправляется автору воркером PLAIN-текстом (без HTML): поэтому
# без тегов, emoji-литералы рендерятся сами.
IDEA_STATUS_NOTIFY = {
    "ru": "📌 Статус твоей идеи изменился\n\nНомер: #{id}\nНовый статус: {status}\n\nСпасибо за предложение!",
    "en": "📌 The status of your idea changed\n\nNumber: #{id}\nNew status: {status}\n\nThanks for the suggestion!",
    "uk": "📌 Статус твоєї ідеї змінився\n\nНомер: #{id}\nНовий статус: {status}\n\nДякуємо за пропозицію!",
}
IDEA_REPLY_NOTIFY = {
    "ru": "✉️ Ответ владельца на твою идею:\n\nНомер: #{id}\n\n{reply}",
    "en": "✉️ The owner replied to your idea:\n\nNumber: #{id}\n\n{reply}",
    "uk": "✉️ Відповідь власника на твою ідею:\n\nНомер: #{id}\n\n{reply}",
}
# ── Идеи («Предложить идею») ─────────────────────────────────────
IDEA_BUTTONS = {
    "ru": {
        "submit": "✍️ Отправить идею",
        "mine": "📋 Мои предложения",
        "back_menu": "⬅️ Назад в меню",
        "send": "✅ Отправить",
        "edit": "✏️ Изменить текст",
        "cancel": "❌ Отменить",
        "back_ideas": "⬅️ К идеям",
        "back_list": "⬅️ К списку",
        "more": "💡 Ещё идею",
    },
    "en": {
        "submit": "✍️ Suggest an idea",
        "mine": "📋 My suggestions",
        "back_menu": "⬅️ Back to the menu",
        "send": "✅ Send",
        "edit": "✏️ Edit the text",
        "cancel": "❌ Cancel",
        "back_ideas": "⬅️ Back to ideas",
        "back_list": "⬅️ Back to the list",
        "more": "💡 Another idea",
    },
    "uk": {
        "submit": "✍️ Надіслати ідею",
        "mine": "📋 Мої пропозиції",
        "back_menu": "⬅️ Назад до меню",
        "send": "✅ Надіслати",
        "edit": "✏️ Змінити текст",
        "cancel": "❌ Скасувати",
        "back_ideas": "⬅️ До ідей",
        "back_list": "⬅️ До списку",
        "more": "💡 Ще ідею",
    },
}

IDEA_OWNER_NEW = {
    "ru": "💡 Новое предложение #{id}\n\nОт: {author} · ТГ {author_id} · {bot_label}\n\nТекст:\n{text}\n\nСтатус: 🆕 Новая",
}

# Preview-режим (идея #1): карточка фото+bio до открытия чата, двойное «да».
# Кнопки без иконок, пока нет doc-id -- правило каталога эмодзи.
PREVIEW_TEXTS = {
    "ru": {
        "caption": "👤 {anon} · {age} · {gender}\n\n{bio}",
        "gender_m": "парень",
        "gender_f": "девушка",
        "yes": "Подошло",
        "no": "Не сейчас",
        "matched": "Обе стороны подтвердили — чат открыт",
        "waited": "Ответ учтён: чат откроется, как только подтвердит вторая сторона",
        "declined": "Продолжаем искать: пара возвращается в очередь",
        "gone": "Предложение уже не действует",
    },
    "en": {
        "caption": "👤 {anon} · {age} · {gender}\n\n{bio}",
        "gender_m": "guy",
        "gender_f": "girl",
        "yes": "Looks good",
        "no": "Not now",
        "matched": "Both confirmed — the chat is open",
        "waited": "Got it: the chat opens once the other side confirms",
        "declined": "Still searching: the pair returns to the queue",
        "gone": "This offer is no longer active",
    },
    "uk": {
        "caption": "👤 {anon} · {age} · {gender}\n\n{bio}",
        "gender_m": "хлопець",
        "gender_f": "дівчина",
        "yes": "Підходить",
        "no": "Не зараз",
        "matched": "Обидві підтвердили — чат відкрито",
        "waited": "Відповідь врахована: чат відкриється, коли підтвердить друг",
        "declined": "Продовжуємо шукати: пара повертається до черги",
        "gone": "Пропозиція вже недійсна",
    },
}
