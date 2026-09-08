import json
from sqlalchemy import select
from models.base import async_session
from models.user import User
from repositories.chats import premium_active
from services.chats import ChatService
from services.matching import MatchingService
from services.payments import available_entitlement
from services.result import ServiceResult
from utils.time import utcnow
import config


class ProfileService:
    async def edit(self, user_id, field, value):
        if field == "gender" and value not in {"male", "female"}:
            return ServiceResult(False, "INVALID")
        if field == "age" and (not isinstance(value, int) or not config.MIN_AGE <= value <= config.MAX_AGE):
            return ServiceResult(False, "INVALID")
        if field not in {"gender", "age"}:
            return ServiceResult(False, "INVALID")
        await MatchingService().cancel(user_id)
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            from models.chat import ActiveParticipant
            await lock_matching(s)
            if await s.get(ActiveParticipant, user_id):
                return ServiceResult(False, "ACTIVE_CHAT")
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            if not user:
                return ServiceResult(False, "NOT_FOUND")
            setattr(user, field, value)
        return ServiceResult(True, "SAVED")

    async def add_photo(self, user_id, file_id):
        async with async_session.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            photos = json.loads(user.extra_photos or "[]")
            if file_id in photos or file_id == user.profile_photo:
                return ServiceResult(True, "SAVED")
            if not user.profile_photo:
                user.profile_photo = file_id
            elif premium_active(user) and len(photos) < config.MAX_PROFILE_PHOTOS-1:
                photos.append(file_id)
            else:
                entitlement = await available_entitlement(s, user_id, ["extra_profile_photo"])
                if not entitlement or len(photos) >= config.MAX_PROFILE_PHOTOS-1:
                    return ServiceResult(False, "PHOTO_LIMIT")
                entitlement.remaining_uses -= 1
                photos.append(file_id)
            user.extra_photos = json.dumps(photos)
        return ServiceResult(True, "SAVED")

    async def delete(self, user_id):
        # Mark unavailable before leaving the queue so a concurrent search cannot rematch.
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            if not user:
                return ServiceResult(False, "NOT_FOUND")
            user.deleted_at = utcnow()
        await MatchingService().cancel(user_id)
        await ChatService().end_chat(user_id, reason="account_deleted")
        async with async_session.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            user.username = user.first_name = user.gender = user.age = user.bio = user.profile_photo = None
            user.extra_photos = "[]"
            user.rules_version = user.rules_accepted_at = None
            user.onboarding_completed, user.onboarding_step = False, "WELCOME"
            user.deleted_at = utcnow()
            user.is_premium, user.premium_expires = False, None
            user.notification_settings = '{"marketing": false}'
        return ServiceResult(True, "DELETED")

    async def marketing(self, user_id, enabled):
        async with async_session.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            if not user:
                return ServiceResult(False, "NOT_FOUND")
            settings = json.loads(user.notification_settings or "{}")
            settings["marketing"] = enabled
            user.notification_settings = json.dumps(settings)
        return ServiceResult(True, "SAVED")
