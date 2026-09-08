from sqlalchemy import select
import config
from models.user import User
from repositories.users import UserRepository
from services.result import ServiceResult
from utils.time import utcnow


def banned(user):
    return bool(user.is_banned and (user.ban_expires is None or user.ban_expires > utcnow()))


def step_for(user):
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
        await self.repository.get_or_create(telegram_id, username, first_name)

        async def apply(user, session):
            if banned(user):
                return ServiceResult(False, "BANNED", {"until": user.ban_expires})
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
            user.deleted_at = None
            return ServiceResult(True, step_for(user))
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
            if action == "begin" and step == "WELCOME":
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
