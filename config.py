import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Second bot: same code, same database, same handlers and same user pool as
# BOT_TOKEN -- only the fallback language differs. Empty disables it.
BOT2_TOKEN = os.getenv("BOT2_TOKEN", "")
BOT2_DEFAULT_LANG = os.getenv("BOT2_DEFAULT_LANG", "uk")
# Язык и поведение primary, когда второй токен снят (бывают и обратные свапы,
# и отмирания). Пусто/не задано = прежние "ru + детект клиента", так что эти
# ручки ничего не меняют, пока их кто-то не откроет.
BOT_DEFAULT_LANG = os.getenv("BOT_DEFAULT_LANG", "")
BOT_DETECT_CLIENT_LANG = os.getenv("BOT_DETECT_CLIENT_LANG", "1") != "0"
# Канал тревог, не зависящий ни от одного из наших токенов: очередь
# уведомлений разносит тот же процесс, что и бота, поэтому при полной
# смерти обоих токенов сообщить владельцу иначе некому. Принимает POST
# с полями content (Discord) и text (Slack).
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS: set[int] = set()
for uid in os.getenv("ADMIN_IDS", "").split(","):
    uid = uid.strip()
    if uid.isdigit():
        ADMIN_IDS.add(int(uid))

# Ссылка на наш чат в меню бота -- url-кнопка, тап не создаёт события.
COMMUNITY_CHAT_URL = os.getenv("COMMUNITY_CHAT_URL", "https://t.me/davinchik_chat1")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost/anon_chat")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Пороговые значения
CHAT_DELAY_NEXT = int(os.getenv("CHAT_DELAY_NEXT", "7"))
ONLINE_UPDATE_INTERVAL = 60
ANTI_FLOOD_DELAY = 0.5
SEARCH_TIMEOUT = 180
# Total time a Premium search may keep re-queueing for a new window.
# 0 lifts the cap and restores an unlimited Premium search.
PREMIUM_SEARCH_TIMEOUT = 180
INACTIVE_CHAT_TTL = 300

# Прогрессивная модерация: подтверждённая жалоба даёт предупреждение.
# Активные предупреждения живут WARN_TTL_DAYS; на пороге -- автоматический
# мут, на следующем -- временный бан. Явное действие админа всегда сильнее.
WARN_TTL_DAYS = int(os.getenv("WARN_TTL_DAYS", "30"))
WARN_MUTE_AT = int(os.getenv("WARN_MUTE_AT", "3"))
WARN_BAN_AT = int(os.getenv("WARN_BAN_AT", "5"))
WARN_MUTE_HOURS = int(os.getenv("WARN_MUTE_HOURS", "24"))
WARN_BAN_HOURS = int(os.getenv("WARN_BAN_HOURS", "168"))

# Друзья после диалога и возврат собеседника.
MAX_FRIENDS = int(os.getenv("MAX_FRIENDS", "50"))
MAX_FRIEND_REQUESTS = int(os.getenv("MAX_FRIEND_REQUESTS", "10"))
FRIEND_REQUEST_TTL_DAYS = int(os.getenv("FRIEND_REQUEST_TTL_DAYS", "7"))
RECONNECT_WINDOW_MINUTES = int(os.getenv("RECONNECT_WINDOW_MINUTES", "10"))

# ОП за подтверждённых рефералов: N активированных друзей дают D дней
# исключения. 0 отключает награду.
OP_REFERRAL_BYPASS_COUNT = int(os.getenv("OP_REFERRAL_BYPASS_COUNT", "3"))
OP_REFERRAL_BYPASS_DAYS = int(os.getenv("OP_REFERRAL_BYPASS_DAYS", "7"))

# Цены Telegram Stars
STARS_INSTANT_SEARCH = int(os.getenv("STARS_INSTANT_SEARCH", "15"))
STARS_REVEAL_ONE = int(os.getenv("STARS_REVEAL_ONE", "25"))
STARS_GENDER_FILTER = int(os.getenv("STARS_GENDER_FILTER", "10"))
STARS_PREMIUM_1M = int(os.getenv("STARS_PREMIUM_1M", "99"))
STARS_PREMIUM_3M = int(os.getenv("STARS_PREMIUM_3M", "249"))
STARS_PREMIUM_12M = int(os.getenv("STARS_PREMIUM_12M", "799"))
STARS_REVEAL_STATUS = int(os.getenv("STARS_REVEAL_STATUS", "10"))
STARS_REVEAL_PRIORITY = int(os.getenv("STARS_REVEAL_PRIORITY", "10"))
STARS_GENDER_FILTER_MONTH = int(os.getenv("STARS_GENDER_FILTER_MONTH", "49"))
STARS_SKIP_NEXT_DELAY = int(os.getenv("STARS_SKIP_NEXT_DELAY", "25"))

# Скидки
REFERRAL_DISCOUNT = 20
STRIK_DISCOUNT_3D = 10
STRIK_DISCOUNT_7D = 20
STRIK_DISCOUNT_30D = 30

# Профиль
MAX_PROFILE_PHOTOS = 3
FREE_PROFILE_PHOTOS = 1
STARS_EXTRA_PHOTO = 15

MIN_AGE = 18
MAX_AGE = 99
CURRENT_RULES_VERSION = 1
AGE_PAGE_SIZE = 12
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")

# Идеи (кнопка «Предложить идею»): серверные лимиты спама,
# переживают перезапуск, но досчитываются по базе, не в памяти.
IDEA_MAX_PER_DAY = int(os.getenv("IDEA_MAX_PER_DAY", "3"))
IDEA_MIN_INTERVAL_SEC = int(os.getenv("IDEA_MIN_INTERVAL_SEC", "60"))
IDEA_MAX_LEN = int(os.getenv("IDEA_MAX_LEN", "2000"))
IDEA_PAGE_SIZE = int(os.getenv("IDEA_PAGE_SIZE", "5"))
IDEA_ADMIN_PAGE_SIZE = int(os.getenv("IDEA_ADMIN_PAGE_SIZE", "8"))

# Сайт: доступ к /api/stats (просмотры/переходы в бота), пусто = эндпоинт выключен.
STATS_ACCESS_KEY = os.getenv("STATS_ACCESS_KEY", "")

# Локальная админка на /admin: контейнер web слушает только 127.0.0.1, но
# nginx однажды может открыть его наружу, поэтому доступ держится на токене,
# а не на адресе. Пусто = админка выключена целиком.
ADMIN_WEB_TOKEN = os.getenv("ADMIN_WEB_TOKEN", "")
# От чьего имени пишутся действия и аудит. 0 = первый из ADMIN_IDS.
ADMIN_WEB_ID = int(os.getenv("ADMIN_WEB_ID", "0"))
