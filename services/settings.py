from sqlalchemy import select
from models.base import async_session
from models.user import User
from repositories.chats import premium_active
from services.payments import available_entitlement
from services.matching import MatchingService
from services.result import ServiceResult
import config


async def bot_value(key, default):
    from models.operations import BotSetting
    async with async_session() as s:
        setting = await s.get(BotSetting, key)
        return setting.value_json["value"] if setting else default


class SettingsService:
    async def save_search(self, user_id, gender=None, age_range=None):
        if gender is not None and gender not in {"a", "m", "f"}:
            return ServiceResult(False, "INVALID")
        if age_range is not None and not config.MIN_AGE <= age_range[0] <= age_range[1] <= config.MAX_AGE:
            return ServiceResult(False, "INVALID")
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            from models.chat import ActiveParticipant
            await lock_matching(s)
            if await s.get(ActiveParticipant,user_id):
                return ServiceResult(False,"ACTIVE_CHAT")
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            if not user:
                return ServiceResult(False,"NOT_FOUND")
            premium = premium_active(user)
            gender_allowed = premium or await available_entitlement(s, user_id, ["gender_filter_one_match", "gender_filter_1m"])
            if (gender not in {None, "a"} and not gender_allowed) or (age_range not in {None, (18,99)} and not premium):
                return ServiceResult(False, "PREMIUM_REQUIRED")
            if gender:
                user.settings_gender_filter = gender
            if age_range:
                user.settings_age_min, user.settings_age_max = age_range
        # Only drop the user's queue entry once the change actually lands --
        # a rejected change (bad input, active chat, no entitlement) must not
        # have the side effect of silently pulling them out of the queue.
        await MatchingService().cancel(user_id)
        return ServiceResult(True, "SAVED")
