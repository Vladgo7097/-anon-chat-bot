"""Read compatibility helpers. Mutations belong in transactional services."""
import json
import logging
from locales import normalize
from repositories.users import UserRepository
from repositories.chats import premium_active

logger = logging.getLogger(__name__)


async def get_or_create_user(telegram_id, username=None, first_name=None):
    user, _ = await UserRepository().get_or_create(telegram_id, username, first_name)
    return user


async def get_user_by_id(telegram_id):
    return await UserRepository().get(telegram_id)


async def user_lang(user_id, default="ru"):
    """Language for messages built outside the dispatcher: the worker's notices
    and bot.py's error trap never see `data["lang"]`, so they ask here. `default`
    carries the client's own language_code when the row has none yet.

    Fails soft on purpose. This only picks a label for a message that already
    exists -- a lookup that raised would take the whole send down with it, which
    is a worse outcome than shipping that one notice in the fallback language.
    """
    try:
        user = await UserRepository().get(user_id) if user_id else None
    except Exception:
        logger.debug("event=user_lang_unavailable user_id=%s", user_id)
        return normalize(default)
    return normalize(getattr(user, "language", None) or default)


async def is_premium(user_id):
    user = await UserRepository().get(user_id)
    return bool(user and premium_active(user))


async def get_user_stats(user_id):
    user = await UserRepository().get(user_id)
    if not user:
        return {}
    return {
        "anon_id": user.anon_id, "created_at": user.created_at,
        "chats_count": user.chats_count, "likes": user.likes_received,
        "dislikes": user.dislikes_received, "rating_pct": user.positive_rating_pct,
        "streak": user.streak_days, "is_premium": premium_active(user),
        "premium_expires": user.premium_expires, "total_spent": user.total_stars_spent,
        "total_chat_seconds": user.total_chat_seconds, "gender": user.gender, "age": user.age,
        "profile_photo": user.profile_photo, "bio": user.bio,
        "extra_photos_count": len(json.loads(user.extra_photos or "[]")),
    }
