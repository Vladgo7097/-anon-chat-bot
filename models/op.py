"""Модели ОП v2 (ТЗ владельца от 19.10.2026).

Настройки, ресурсы, правила с группами AND/OR, шаблоны, исключения,
аудит и журнал ошибок. Всё скоупится по bot_id: площадки две, и
getChatMember чужие подписки отдаёт только администратору чата.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime as DateTime
from utils.time import utcnow


class OpSettings(Base):
    """Одна строка на бота: тумблеры, окно требования и подписи кнопок.

    Сроки здесь -- окно требования целиком; у ресурсов и правил свои окна.
    Просроченное окно не блокирует: сервис вычисляет активность на месте,
    поэтому рестарт не может оставить старую ОП включённой.
    """

    __tablename__ = "op_settings"

    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    check_after_registration: Mapped[bool] = mapped_column(Boolean, default=True)
    check_each_action: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Kyiv")
    btn_join: Mapped[str | None] = mapped_column(Text, nullable=True)
    btn_check: Mapped[str | None] = mapped_column(Text, nullable=True)
    btn_back: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpTarget(Base):
    """Канал или группа требования.

    join_url нужен пользователю для вступления, chat_id -- для проверки
    через getChatMember. setup_status фиксирует состояние подключения,
    last_error -- последнюю проблему (нет прав, чат недоступен).
    """

    __tablename__ = "op_targets"
    __table_args__ = (
        UniqueConstraint("bot_id", "chat_id", name="uq_op_targets_bot_chat"),
        Index("ix_op_targets_bot_active", "bot_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(Text, default="")
    chat_type: Mapped[str] = mapped_column(String(16), default="channel")
    visibility: Mapped[str] = mapped_column(String(16), default="public")
    join_url: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    setup_status: Mapped[str] = mapped_column(String(16), default="pending")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpPolicy(Base):
    """Правило: '*' -- глобальное требование, иначе -- для функции."""

    __tablename__ = "op_policies"
    __table_args__ = (Index("ix_op_policies_bot_feature", "bot_id", "feature_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    feature_key: Mapped[str] = mapped_column(String(64), default="*")
    title: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpRuleGroup(Base):
    """Группа условий: 'and' -- все ресурсы, 'or' -- достаточно любого."""

    __tablename__ = "op_rule_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("op_policies.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(String(8), default="and")
    position: Mapped[int] = mapped_column(Integer, default=0)


class OpRuleTarget(Base):
    """Связь ресурса с группой правила."""

    __tablename__ = "op_rule_targets"
    __table_args__ = (
        UniqueConstraint("group_id", "target_id", name="uq_op_rule_targets"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("op_rule_groups.id", ondelete="CASCADE")
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("op_targets.id", ondelete="CASCADE")
    )


class OpMessageTemplate(Base):
    """Текст экрана ОП: global, для функции (feature_key) или ошибки."""

    __tablename__ = "op_message_templates"
    __table_args__ = (
        Index(
            "uq_op_message_templates_scope",
            "bot_id",
            "scope",
            text("coalesce(feature_key, '')"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    scope: Mapped[str] = mapped_column(String(16), default="global")
    feature_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    format: Mapped[str] = mapped_column(String(16), default="html")
    updated_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpExemption(Base):
    """Исключение: пользователь обходит ОП до until (None -- бессрочно)."""

    __tablename__ = "op_exemptions"
    __table_args__ = (
        UniqueConstraint("bot_id", "user_id", name="uq_op_exemptions_bot_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpAuditLog(Base):
    """История действий владельца в панели ОП."""

    __tablename__ = "op_audit_log"
    __table_args__ = (Index("ix_op_audit_bot_time", "bot_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)


class OpErrorLog(Base):
    """Ошибки Telegram API и диагностика ресурсов."""

    __tablename__ = "op_error_log"
    __table_args__ = (Index("ix_op_errors_bot_time", "bot_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
