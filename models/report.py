from utils.time import utcnow
from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class ReportSnapshot(Base):
    """Media and text captured only when a report is actually filed.

    The relay buffer keeps the last eight messages for thirty minutes; this
    table is the durable copy a moderator can open later. Media is kept for
    thirty days and then purged by the maintenance worker -- enough to review
    a complaint, short enough not to become a content archive.
    """

    __tablename__ = "report_snapshots"
    __table_args__ = (
        Index("ix_report_snapshots_report", "report_id"),
        Index("ix_report_snapshots_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16), default="text")
    file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
