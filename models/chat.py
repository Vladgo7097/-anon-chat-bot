from datetime import datetime
from sqlalchemy import BigInteger, Integer, DateTime, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user1_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user2_id: Mapped[int] = mapped_column(BigInteger, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
