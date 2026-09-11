import re
from sqlalchemy import select
import config
from models.user import User
from repositories.users import UserRepository
from services.result import ServiceResult
from utils.time import utcnow


def banned(user):
    return bool(user.is_banned and (user.ban_expires is None or user.ban_expires > utcnow()))


def step_for(user):
    # Only genuinely new/incomplete accounts are sent through LANGUAGE --
    # a user who already finished onboarding before this column existed
    # (language is NULL until the migration's backfill or a manual choice)
    # must not be knocked back into onboarding just because it's unset.
    if not getattr(user, "language", None) and not user.onboarding_completed:
        return "LANGUAGE"
    if user.gender not in {"male", "female"}:
        return "WELCOME" if user.onboarding_step == "WELCOME" else "GENDER"
    if user.age is None or not config.MIN_AGE <= user.age <= config.MAX_AGE:
        return "AGE"
    if not user.rules_accepted_at or (user.rules_version or 0) < config.CURRENT_RULES_VERSION:
        return "RULES"
    return "MENU" if user.onboarding_completed else "RULES"


class OnboardingService:
    def __init__(self, repository=None):
        self.repository = repository or UserRepository()

    async def start(self, telegram_id, username=None, first_name=None, payload="", notify_owner=False):
        _, is_new = await self.repository.get_or_create(telegram_id, username, first_name)

        async def apply(user, session):
            if banned(user):
                return ServiceResult(False, "BANNED", {"until": user.ban_expires, "language": getattr(user, "language", None)})
            if payload.startswith("ref_") and not user.referrer_id and not user.onboarding_completed:
                value = payload.removeprefix("ref_")
                if value.isalnum() and len(value)<=32:
                    # Public referral payloads use the random anonymous code.
                    # Raw Telegram IDs are deliberately not accepted.
                    referrer = await session.scalar(select(User).where(
                        User.referral_code == value).with_for_update())
                    if referrer and referrer.telegram_id != telegram_id:
                        user.referrer_id = referrer.telegram_id
                        from models.operations import Referral
                        referral = await session.scalar(select(Referral).where(Referral.referred_id == telegram_id))
                        if not referral:
                            session.add(Referral(referrer_id=referrer.telegram_id, referred_id=telegram_id))
                            referrer.referrals_count += 1
            if is_new and isinstance(self.repository, UserRepository):
                # Every non-"ref_" /start payload used to be read and thrown away,
                # which made it impossible to tell a low-intent bot-catalog
                # visitor from a genuinely broken first screen. Telegram itself
                # restricts start parameters to this character set already; the
                # regex is a defensive cap, not a real sanitization need.
                from services.growth import event
                source = re.sub(r"[^A-Za-z0-9_-]", "", payload)[:48] or "none"
                await event(session, telegram_id, "start_source", f"start_source:{telegram_id}:{source}")
            user.deleted_at = None
            return ServiceResult(True, step_for(user), {"language": getattr(user, "language", None)})
        result = await self.repository.transition(telegram_id, apply)
        if result.code == "MENU" and isinstance(self.repository, UserRepository):
            from services.growth import StreakService
            await StreakService().checkin(telegram_id)
        return result

    async def advance(self, telegram_id, action, value=None):
        async def apply(user, session):
            if not user:
                return ServiceResult(False, "WELCOME")
            if banned(user):
                return ServiceResult(False, "BANNED", {"until": user.ban_expires})
            step = step_for(user)
            if step == "MENU":
                return ServiceResult(True, "MENU")
            # A deleted account re-entering onboarding through plain button
            # presses (not /start) must not stay permanently excluded from
            # matching once it reaches MENU again — start() clears this for
            # the /start path, advance() must for every other one.
            user.deleted_at = None
            if action == "lang" and step == "LANGUAGE" and value in {"ru", "en", "uk"}:
                user.language = value
            elif action == "begin" and step == "WELCOME":
                user.onboarding_step = "GENDER"
            elif action == "gender" and step == "GENDER" and value in {"male", "female"}:
                user.gender, user.onboarding_step = value, "AGE"
            elif action == "age" and step == "AGE":
                if not isinstance(value, int) or not config.MIN_AGE <= value <= config.MAX_AGE:
                    if isinstance(self.repository, UserRepository):
                        # Counted once per user per day: the funnel is built from
                        # the users table, which keeps no trace of a refused answer.
                        from services.growth import event
                        await event(session, telegram_id, "onboarding_age_rejected",
                                    f"age_rejected:{telegram_id}:{utcnow():%Y-%m-%d}")
                    return ServiceResult(False, "AGE", {"error": "Сервис доступен только с 18 лет. Выбери возраст от 18 до 99."})
                user.age, user.onboarding_step = value, "RULES"
            elif action == "accept" and step == "RULES":
                user.rules_version = config.CURRENT_RULES_VERSION
                user.rules_accepted_at = utcnow()
                user.onboarding_completed, user.onboarding_step = True, "MENU"
                if isinstance(self.repository, UserRepository):
                    from services.growth import event
                    await event(session, telegram_id, "onboarding_completed", f"onboarding:{telegram_id}")
            return ServiceResult(True, step_for(user))
        return await self.repository.transition(telegram_id, apply)

    async def set_language(self, telegram_id, value):
        """Change language from Settings, outside the onboarding flow -- does
        not touch onboarding_step, unlike advance()'s "lang" action."""
        if value not in {"ru", "en", "uk"}:
            return ServiceResult(False, "INVALID")

        async def apply(user, session):
            if not user:
                return ServiceResult(False, "NOT_FOUND")
            user.language = value
            return ServiceResult(True, "SAVED")
        return await self.repository.transition(telegram_id, apply)
