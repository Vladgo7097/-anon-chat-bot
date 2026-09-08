from utils.time import utcnow
from datetime import datetime
from sqlalchemy import BigInteger, Integer, DateTime, String, Text, Boolean, ForeignKey, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (CheckConstraint("user1_id <> user2_id", name="ck_chat_distinct_users"),
                      Index("ix_chats_user1_started", "user1_id", "started_at"),
                      Index("ix_chats_user2_started", "user2_id", "started_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ended", server_default="ended")
    ended_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user1_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user2_id: Mapped[int] = mapped_column(BigInteger, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)

    # Оценки
    user1_rating: Mapped[str | None] = mapped_column(String(1), nullable=True)
    user2_rating: Mapped[str | None] = mapped_column(String(1), nullable=True)

    # Раскрытие контакта
    reveal_requested_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reveal_mutual: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_id: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(32), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActiveParticipant(Base):
    __tablename__ = "active_participants"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chats.session_id"), index=True)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("chat_session_id", "rater_id"), CheckConstraint("value IN (-1, 1)"))
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_session_id: Mapped[str] = mapped_column(ForeignKey("chats.session_id"), index=True)
    rater_id: Mapped[int] = mapped_column(BigInteger)
    rated_id: Mapped[int] = mapped_column(BigInteger)
    value: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Block(Base):
    __tablename__ = "blocks"
    blocker_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    blocked_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str] = mapped_column(String(255), default="report")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RevealRequest(Base):
    __tablename__ = "reveal_requests"
    chat_session_id: Mapped[str] = mapped_column(ForeignKey("chats.session_id"), primary_key=True)
    consent_a: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_b: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
