from sqlalchemy import BigInteger, String, Integer, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class Idea(Base):
    """Пользовательское предложение: что бы человек добавил в бота.

    Один пользователь умеет читать только свои строки - проверка автора
    живёт в обработчиках, а не в данных (в базе нет чужих имён).
    Статусы - словарь IDEA_STATUSES в handlers/ideas.py, единый для панели
    и карточек.
    """

    __tablename__ = "idea_suggestions"
    __table_args__ = (
        Index("ix_ideas_user_created", "telegram_user_id", "created_at"),
        Index("ix_ideas_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    # new -> review -> planned -> in_progress -> implemented
    #   с уходом в rejected / duplicate на любом живом шаге.
    status: Mapped[str] = mapped_column(String(16), default="new")
    bot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    reviewed_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_reply_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    # Заполняется, когда владелец помечает предложение дубликатом.
    duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
