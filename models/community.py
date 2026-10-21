"""Community features: progressive warnings, friendships, partner return.

All three are deliberately kept apart from the chat/relay tables: a warning
outlives the report that caused it, a friendship outlives the dialogue it
started in, and a return offer is a ten-minute window tied to one ended
session. Telegram ids are stored as-is; no row here ever needs a profile.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class UserWarning(Base):
    """One moderation warning. Expired warnings stay for the audit trail but
    stop counting toward the progressive thresholds."""

    __tablename__ = "user_warnings"
    __table_args__ = (
        UniqueConstraint("report_id", name="uq_user_warnings_report"),
        Index("ix_user_warnings_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(255), default="")
    issued_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime())


class Friendship(Base):
    """A pair of users, stored in ascending-id order so one pair has one row.

    `requested_by` is the side that must wait for the other's answer; on a
    reciprocal request the second tap accepts immediately instead of creating
    a second pending row.
    """

    __tablename__ = "friendships"
    __table_args__ = (
        CheckConstraint("user1_id < user2_id", name="ck_friendship_order"),
        UniqueConstraint("user1_id", "user2_id", name="uq_friendship_pair"),
        Index("ix_friendships_user1_status", "user1_id", "status"),
        Index("ix_friendships_user2_status", "user2_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user1_id: Mapped[int] = mapped_column(BigInteger)
    user2_id: Mapped[int] = mapped_column(BigInteger)
    requested_by: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class ReconnectOffer(Base):
    """A short window after a voluntary end during which both sides can
    return to the same partner. One row per chat session."""

    __tablename__ = "reconnect_offers"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_reconnect_offers_session"),
        Index("ix_reconnect_offers_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32))
    user1_id: Mapped[int] = mapped_column(BigInteger)
    user2_id: Mapped[int] = mapped_column(BigInteger)
    user1_wants: Mapped[bool] = mapped_column(Boolean, default=False)
    user2_wants: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime())
