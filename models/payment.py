from utils.time import utcnow
from datetime import datetime
from sqlalchemy import BigInteger, Integer, DateTime, String, Text, Boolean, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product: Mapped[str] = mapped_column(String(64))  # instant_search, reveal, gender_filter, premium_1m, etc.
    stars_amount: Mapped[int] = mapped_column(Integer)
    telegram_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    payload: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="XTR", server_default="XTR")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    target_session: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    precheckout_query_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invoice_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="created")


class PremiumSubscription(Base):
    __tablename__ = "premium_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plan: Mapped[str] = mapped_column(String(32))  # 1m, 3m, 12m
    stars_paid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(default=True)
    auto_renew: Mapped[bool] = mapped_column(default=False)
    source_payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)


class Product(Base):
    __tablename__ = "products"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    price_stars: Mapped[int] = mapped_column(Integer)
    product_type: Mapped[str] = mapped_column(String(32))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Entitlement(Base):
    __tablename__ = "entitlements"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    remaining_uses: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True)
    target_session: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
