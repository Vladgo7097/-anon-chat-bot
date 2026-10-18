from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class OpSettings(Base):
    """Конфигурация ОП: одна строка на бота.

    Это единственное место, где живёт состояние требования: enabled -- это
    тумблер владельца, starts_at/ends_at -- окно. Что ОП реально включена,
    решает is_active() в services/op.py на каждом запросе, поэтому рестарт
    не может оставить просроченную ОП блокирующей.
    """

    __tablename__ = "op_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Новая кампания на каждое ручное включение: уведомления дедуплицируются
    # в пределах кампании, поэтому повторные рассылки не множат дубликаты,
    # а следующее включение может уведомить людей заново.
    campaign_id: Mapped[int] = mapped_column(Integer, default=0)
    notify_running: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    notify_done: Mapped[int] = mapped_column(Integer, default=0)
    notify_sent: Mapped[int] = mapped_column(Integer, default=0)
    notify_total: Mapped[int] = mapped_column(Integer, default=0)
    checks_total: Mapped[int] = mapped_column(Integer, default=0)
    checks_blocked: Mapped[int] = mapped_column(Integer, default=0)
    checks_error: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpChat(Base):
    """Ресурс требования -- канал или группа.

    rights_ok фиксирует последнюю проверку: getChatMember для чужих подписок
    работает только у администратора чата, поэтому ресурс без прав не
    активируется и не спасает от ошибок проверки.
    """

    __tablename__ = "op_chats"
    __table_args__ = (
        UniqueConstraint("bot_id", "chat_id", name="uq_op_chats_bot_chat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(16), default="")
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    rights_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
