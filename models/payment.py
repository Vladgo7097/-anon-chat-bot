from datetime import datetime
from sqlalchemy import BigInteger, Integer, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product: Mapped[str] = mapped_column(String(64))  # instant_search, reveal, gender_filter, premium_1m, etc.
    stars_amount: Mapped[int] = mapped_column(Integer)
    telegram_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16), default="completed")  # completed/refunded


class PremiumSubscription(Base):
    __tablename__ = "premium_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan: Mapped[str] = mapped_column(String(32))  # 1m, 3m, 12m
    stars_paid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(default=True)
    auto_renew: Mapped[bool] = mapped_column(default=False)
