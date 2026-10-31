from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class PreviewOffer(Base):
    """Короткое «посмотри, потом сойдёмся» между двумя ищущими.

    Одна строка на пару (канонический порядок user1_id < user2_id), живёт до
    expires_at. Пока status='requested', обе стороны зарезервированы от чужих
    матчей — краевой случай ActiveParticipant, только мягкий и по времени.
    Отказ или тишина уводят пару в кулдаун, двойное «да» открывает чат.

    Двойной unique-индекс на активную пару - закон базы: дубликат оффера
    запрещён даже в гейм-расе, поэтому предложение создаётся только
    SELECT-then-INSERT под lock_matching.
    """

    __tablename__ = "preview_offers"
    __table_args__ = (
        CheckConstraint("user1_id < user2_id", name="ck_preview_canonical_pair"),
        Index(
            "ix_preview_active_pair",
            "user1_id",
            "user2_id",
            unique=True,
            postgresql_where=text("status = 'requested'"),
        ),
        Index("ix_preview_user1", "user1_id"),
        Index("ix_preview_user2", "user2_id"),
        Index("ix_preview_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user1_id: Mapped[int] = mapped_column(BigInteger)
    user2_id: Mapped[int] = mapped_column(BigInteger)
    consent_1: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_2: Mapped[bool] = mapped_column(Boolean, default=False)
    # requested | mutual | declined | expired | canceled
    status: Mapped[str] = mapped_column(String(16), default="requested")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Заполняется при mutual: чат создан той же транзакцией согласия.
    resolved_session: Mapped[str | None] = mapped_column(String(32), nullable=True)
