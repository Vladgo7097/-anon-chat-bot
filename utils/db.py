"""Read compatibility helpers. Mutations belong in transactional services."""
import json
from repositories.users import UserRepository
from repositories.chats import premium_active


async def get_or_create_user(telegram_id, username=None, first_name=None):
    user, _ = await UserRepository().get_or_create(telegram_id, username, first_name)
    return user


async def get_user_by_id(telegram_id):
    return await UserRepository().get(telegram_id)


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
