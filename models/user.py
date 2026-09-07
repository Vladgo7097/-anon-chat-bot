import secrets

from utils.time import utcnow
from datetime import datetime, date
from sqlalchemy import BigInteger, String, Integer, DateTime, Date, Boolean, Float, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    anon_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)  # #48213
    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True,
                                               default=lambda: secrets.token_hex(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Необязательный профиль
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    onboarding_step: Mapped[str] = mapped_column(String(16), default="WELCOME", server_default="WELCOME")
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rules_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rules_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bot_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_photo: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extra_photos: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of file_ids

    # Статистика
    chats_count: Mapped[int] = mapped_column(Integer, default=0)
    likes_received: Mapped[int] = mapped_column(Integer, default=0)
    dislikes_received: Mapped[int] = mapped_column(Integer, default=0)
    total_chat_seconds: Mapped[int] = mapped_column(Integer, default=0)

    # Premium
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Реферальная программа
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    referrals_count: Mapped[int] = mapped_column(Integer, default=0)
    referrals_activated: Mapped[int] = mapped_column(Integer, default=0)

    # Стрик
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    streak_rewards_claimed: Mapped[str] = mapped_column(Text, default="[]")  # JSON

    # Рейтинг
    positive_rating_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Активность
    last_online: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Бан
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Прочее
    settings_gender_filter: Mapped[str] = mapped_column(String(1), default="a")  # m/f/a (all)
    settings_age_min: Mapped[int] = mapped_column(Integer, default=18)
    settings_age_max: Mapped[int] = mapped_column(Integer, default=99)
    total_stars_spent: Mapped[int] = mapped_column(Integer, default=0)
    notification_settings: Mapped[str] = mapped_column(Text, default="{}")  # JSON

    def __repr__(self):
        return f"<User #{self.anon_id} tg={self.telegram_id}>"
