import random
from datetime import datetime, date
from typing import Optional

from sqlalchemy import select, update, func
from models.base import async_session
from models.user import User
from models.chat import Chat
from models.report import Report
from models.ban import Ban
from models.payment import Payment, PremiumSubscription


def generate_anon_id() -> str:
    return f"#{random.randint(10000, 99999)}"


async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None) -> User:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                telegram_id=telegram_id,
                anon_id=generate_anon_id(),
                username=username,
                first_name=first_name,
                last_active_date=date.today(),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            user.last_online = datetime.utcnow()
            user.last_active_date = date.today()
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
            await session.commit()

        return user


async def update_user(telegram_id: int, **kwargs) -> None:
    async with async_session() as session:
        await session.execute(
            update(User).where(User.telegram_id == telegram_id).values(**kwargs)
        )
        await session.commit()


async def get_user_by_id(telegram_id: int) -> Optional[User]:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


async def get_user_by_anon_id(anon_id: str) -> Optional[User]:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.anon_id == anon_id)
        )
        return result.scalar_one_or_none()


async def increment_chats(telegram_id: int):
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )).scalar_one_or_none()
        if user:
            user.chats_count += 1
            await session.commit()


async def add_rating(user_id: int, is_positive: bool):
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if user:
            if is_positive:
                user.likes_received += 1
            else:
                user.dislikes_received += 1
            total = user.likes_received + user.dislikes_received
            if total > 0:
                user.positive_rating_pct = round(user.likes_received / total * 100, 1)
            await session.commit()


async def save_chat(user1_id: int, user2_id: int, duration: int = 0) -> Chat:
    async with async_session() as session:
        chat = Chat(
            user1_id=user1_id,
            user2_id=user2_id,
            ended_at=datetime.utcnow(),
            duration_seconds=duration,
        )
        session.add(chat)

        # BUG FIX: increment total_chat_seconds for both users
        for uid in (user1_id, user2_id):
            user = (await session.execute(
                select(User).where(User.telegram_id == uid)
            )).scalar_one_or_none()
            if user:
                user.total_chat_seconds += duration

        await session.commit()
        await session.refresh(chat)
        return chat


async def save_report(reporter_id: int, reported_id: int, reason: str, chat_id: int = None, message_content: str = None) -> Report:
    async with async_session() as session:
        report = Report(
            reporter_id=reporter_id,
            reported_id=reported_id,
            chat_id=chat_id,
            reason=reason,
            message_content=message_content,
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        return report


async def get_pending_reports() -> list[Report]:
    async with async_session() as session:
        result = await session.execute(
            select(Report).where(Report.status == "pending").order_by(Report.created_at)
        )
        return list(result.scalars().all())


async def resolve_report(report_id: int, admin_id: int, status: str = "resolved"):
    async with async_session() as session:
        report = (await session.execute(
            select(Report).where(Report.id == report_id)
        )).scalar_one_or_none()
        if report:
            report.status = status
            report.resolved_at = datetime.utcnow()
            report.resolved_by = admin_id
            await session.commit()


async def ban_user(user_id: int, reason: str, duration_hours: int = None, admin_id: int = None, report_id: int = None):
    async with async_session() as session:
        from datetime import timedelta
        expires = datetime.utcnow() + timedelta(hours=duration_hours) if duration_hours else None
        ban = Ban(
            user_id=user_id,
            reason=reason,
            duration_hours=duration_hours,
            expires_at=expires,
            banned_by=admin_id,
            report_id=report_id,
        )
        session.add(ban)
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if user:
            user.is_banned = True
            user.ban_expires = expires
        await session.commit()


async def check_ban(user_id: int) -> bool:
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if not user or not user.is_banned:
            return False
        if user.ban_expires and user.ban_expires < datetime.utcnow():
            user.is_banned = False
            user.ban_expires = None
            await session.commit()
            return False
        return True


async def add_payment(user_id: int, product: str, stars: int, payment_id: str = None) -> Payment:
    async with async_session() as session:
        payment = Payment(
            user_id=user_id,
            product=product,
            stars_amount=stars,
            telegram_payment_id=payment_id,
        )
        session.add(payment)
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if user:
            user.total_stars_spent += stars
        await session.commit()
        await session.refresh(payment)
        return payment


async def activate_premium(user_id: int, plan: str, stars: int):
    from datetime import timedelta
    durations = {"1m": 30, "3m": 90, "12m": 365}
    days = durations.get(plan, 30)

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if user:
            now = datetime.utcnow()
            if user.is_premium and user.premium_expires and user.premium_expires > now:
                user.premium_expires = user.premium_expires + timedelta(days=days)
            else:
                user.premium_expires = now + timedelta(days=days)
            user.is_premium = True

            sub = PremiumSubscription(
                user_id=user_id,
                plan=plan,
                stars_paid=stars,
                expires_at=user.premium_expires,
            )
            session.add(sub)
            await session.commit()


async def is_premium(user_id: int) -> bool:
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if not user or not user.is_premium:
            return False
        if user.premium_expires and user.premium_expires < datetime.utcnow():
            user.is_premium = False
            await session.commit()
            return False
        return True


async def get_user_stats(user_id: int) -> dict:
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == user_id)
        )).scalar_one_or_none()
        if not user:
            return {}
        return {
            "anon_id": user.anon_id,
            "created_at": user.created_at,
            "chats_count": user.chats_count,
            "likes": user.likes_received,
            "dislikes": user.dislikes_received,
            "rating_pct": user.positive_rating_pct,
            "streak": user.streak_days,
            "is_premium": user.is_premium,
            "premium_expires": user.premium_expires,
            "total_spent": user.total_stars_spent,
            "total_chat_seconds": user.total_chat_seconds,
            "gender": user.gender,
            "age": user.age,
        }


async def get_total_users() -> int:
    async with async_session() as session:
        result = await session.execute(select(func.count(User.id)))
        return result.scalar()


async def get_new_users_today() -> int:
    async with async_session() as session:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            select(func.count(User.id)).where(User.created_at >= today)
        )
        return result.scalar()


async def get_total_chats_today() -> int:
    async with async_session() as session:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            select(func.count(Chat.id)).where(Chat.started_at >= today)
        )
        return result.scalar()


async def get_total_stars_today() -> int:
    async with async_session() as session:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            select(func.coalesce(func.sum(Payment.stars_amount), 0)).where(Payment.created_at >= today)
        )
        return result.scalar()


async def get_premium_count() -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count(User.id)).where(User.is_premium == True)
        )
        return result.scalar()
