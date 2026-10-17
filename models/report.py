from utils.time import utcnow
from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("chat_session_id", "reporter_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reported_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(String(64))
    message_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/resolved/dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ReportAI(Base):
    """Вердикт локальной модели по жалобе (бета-модерация).

    Строку пишет вотчер на хосте (deploy/ai/watcher.py), читает панель
    «Жалобы». PK на report_id против задвоения при повторных проходах:
    он же делает вставку вотчера идемпотентной под ON CONFLICT DO NOTHING.
    """

    __tablename__ = "report_ai"

    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
