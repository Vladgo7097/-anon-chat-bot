import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS: set[int] = set()
for uid in os.getenv("ADMIN_IDS", "").split(","):
    uid = uid.strip()
    if uid.isdigit():
        ADMIN_IDS.add(int(uid))

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

# Веб-сайт (Telegram Login Widget / Mini App)
BOT_USERNAME = os.getenv("BOT_USERNAME", "anonim_chatqbot")
WEB_SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "")
WEB_SESSION_MAX_AGE = int(os.getenv("WEB_SESSION_MAX_AGE", str(30 * 86400)))
# Off only for local http:// testing before the real domain/TLS exist.
WEB_COOKIE_SECURE = os.getenv("WEB_COOKIE_SECURE", "1") == "1"

# Звонки. Медиа идёт через свой TURN, чтобы собеседники не видели IP друг друга.
TURN_HOST = os.getenv("TURN_HOST", "")
TURN_REALM = os.getenv("TURN_REALM", TURN_HOST)
TURN_SECRET = os.getenv("TURN_SECRET", "")
TURN_CREDENTIAL_TTL = int(os.getenv("TURN_CREDENTIAL_TTL", "600"))
# Комната открывается по одноразовой ссылке: человек из бота может вообще не
# иметь веб-сессии, а звонок ждать не может.
CALL_TOKEN_TTL = int(os.getenv("CALL_TOKEN_TTL", "300"))
# Сколько ждём ответа на предложение звонка, прежде чем снять его.
CALL_OFFER_TTL = int(os.getenv("CALL_OFFER_TTL", "60"))
