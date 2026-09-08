from utils.time import utcnow
from datetime import datetime
from sqlalchemy import BigInteger, Integer, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base, UTCDateTime as DateTime


class Ban(Base):
    __tablename__ = "bans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reason: Mapped[str] = mapped_column(String(64))
    duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = permanent
    banned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    banned_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
