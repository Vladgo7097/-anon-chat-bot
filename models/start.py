from datetime import datetime

from sqlalchemy import BigInteger, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class ProcessedUpdate(Base):
    """Один доставленный апдейт Telegram -- одна строка.

    Ключ составной: два бота опрашиваются независимо, их update_id
    пересекаются, и общий ключ по одному update_id ронял бы настоящие
    апдейты второго бота как «дубликаты» первого.
    """

    __tablename__ = "processed_updates"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    update_type: Mapped[str] = mapped_column(String(32), default="")
    processed_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class StartEvent(Base):
    """История уникальных /start: первый -- first_start, остальные repeat_start.

    update_id уникален в пределах бота: одному апдейту не может
    соответствовать два события запуска, даже при повторной доставке.
    """

    __tablename__ = "start_events"
    __table_args__ = (
        UniqueConstraint("bot_id", "update_id", name="uq_start_events_update"),
        Index("idx_start_events_created_at", "created_at"),
        Index("idx_start_events_user_created", "telegram_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    update_id: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
