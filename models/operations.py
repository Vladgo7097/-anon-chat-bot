from datetime import datetime, date
from sqlalchemy import String, Integer, BigInteger, Boolean, Text, JSON, Date, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class BotSetting(Base):
    __tablename__ = "bot_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AdminAudit(Base):
    __tablename__ = "admin_audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    request_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64))
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger)
    referred_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class Streak(Base):
    __tablename__ = "streaks"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    best_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_checkin_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Achievement(Base):
    __tablename__ = "achievements"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    achievement_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_scope: Mapped[str] = mapped_column(String(64), default="any")
    discount_percent: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime())
    status: Mapped[str] = mapped_column(String(16), default="active")
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    reserved_payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Notification(Base):
    __tablename__ = "notification_log"
    # Serves the worker's dispatch query, which runs several times a second:
    # pending rows ordered by marketing first, then id.
    __table_args__ = (Index("ix_notification_dispatch", "marketing", "id",
                            postgresql_where=text("status IN ('queued', 'sending')")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    text: Mapped[str] = mapped_column(Text)
    callback_data: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    broadcast_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    segment: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    progress_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class AdCampaign(Base):
    """A cross-promo slot sold to another Telegram channel, shown between chats.

    Revenue here is external (the advertiser pays outside Stars); the bot only
    tracks impressions, and joins when the channel granted admin rights, against
    the agreed budget. Telegram does not notify a bot when a plain url= button
    is tapped, so clicks are never measured or fabricated (see product rule
    against fictitious monetization events).
    """
    __tablename__ = "ad_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    channel_username: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(64))
    subtitle: Mapped[str] = mapped_column(String(64), default="")
    body: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|active|paused|completed
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)  # impressions/day, 0 = unlimited
    total_limit: Mapped[int] = mapped_column(Integer, default=0)  # lifetime impressions, 0 = unlimited
    impressions_total: Mapped[int] = mapped_column(Integer, default=0)
    # Set only when the bot could create a tracked invite link (needs admin
    # rights in the advertiser's channel); NULL means the plain channel link
    # was used and joins genuinely cannot be attributed to this campaign.
    invite_link: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    joins_total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class AdImpression(Base):
    """One durable, billable record of a campaign shown to a user."""
    __tablename__ = "ad_impressions"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    shown_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow, index=True)


class AdJoin(Base):
    """A real, Telegram-attributed join to the advertiser's channel via the
    campaign's own tracked invite link — never inferred or estimated."""
    __tablename__ = "ad_joins"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
