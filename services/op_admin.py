"""ОП v2: панель управления -- данные и проверки, без текстов и кнопок.

Все операции скоупятся по bot_id: конфигурация у каждого бота своя.
Каждое изменение конфигурации пишет запись в op_audit_log; проверки
прав бота -- в op_targets.setup_status/last_error. Тексты экранов и
клавиатуры живут в handlers/op_admin.py и keyboards/inline.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select

from models.base import async_session
from models.op import (
    OpAuditLog,
    OpErrorLog,
    OpPolicy,
    OpRuleGroup,
    OpRuleTarget,
    OpTarget,
)
from services import op as core
from utils.time import utcnow

logger = logging.getLogger(__name__)

INVITE_NAME = "Обязательная подписка"


async def audit(s, bot_id, actor_id, action: str, payload: dict | None = None) -> None:
    s.add(
        OpAuditLog(
            bot_id=bot_id,
            actor_id=actor_id,
            action=action,
            payload=payload or {},
        )
    )


async def overview(bot_id: int) -> dict:
    """Состояние панели: настройки, ресурсы, правила и живость требования."""
    now = utcnow()
    async with async_session() as s:
        settings = await core.settings_or_create(s, bot_id)
        await s.commit()
        targets = list(
            (
                await s.scalars(
                    select(OpTarget)
                    .where(OpTarget.bot_id == bot_id)
                    .order_by(OpTarget.position, OpTarget.id)
                )
            ).all()
        )
        policies = list(
            (
                await s.scalars(
                    select(OpPolicy)
                    .where(OpPolicy.bot_id == bot_id)
                    .order_by(OpPolicy.position, OpPolicy.id)
                )
            ).all()
        )
        live_policies = [
            policy
            for policy in policies
            if policy.enabled
            and core.window_live(policy.starts_at, policy.ends_at, now)
        ]
        live_targets = {
            target.id
            for target in targets
            if target.is_active
            and core.window_live(target.starts_at, target.ends_at, now)
        }
        required = False
        if core.settings_live(settings, now) and live_policies and live_targets:
            policy_ids = [policy.id for policy in live_policies]
            groups = list(
                (
                    await s.scalars(
                        select(OpRuleGroup).where(OpRuleGroup.policy_id.in_(policy_ids))
                    )
                ).all()
            )
            if groups:
                links = list(
                    (
                        await s.scalars(
                            select(OpRuleTarget).where(
                                OpRuleTarget.group_id.in_(
                                    [group.id for group in groups]
                                )
                            )
                        )
                    ).all()
                )
                required = any(link.target_id in live_targets for link in links)
        errors_24h = await s.scalar(
            select(func.count(OpErrorLog.id)).where(
                OpErrorLog.bot_id == bot_id,
                OpErrorLog.created_at >= now - timedelta(hours=24),
            )
        )
    return {
        "settings": settings,
        "targets": targets,
        "policies": policies,
        "live_policies": live_policies,
        "live_targets": live_targets,
        "required": required,
        "errors_24h": errors_24h or 0,
    }


async def set_flag(bot_id: int, actor_id: int, field: str, value: bool):
    """Тумблеры enabled/paused; updated_at двигается, конфиг не теряется."""
    if field not in {"enabled", "paused"}:
        raise ValueError(f"unknown settings field: {field}")
    async with async_session.begin() as s:
        settings = await core.settings_or_create(s, bot_id)
        setattr(settings, field, value)
        settings.updated_at = utcnow()
        await audit(s, bot_id, actor_id, f"{field}:{int(value)}")
    return settings


async def list_targets(bot_id: int) -> list[OpTarget]:
    async with async_session() as s:
        return list(
            (
                await s.scalars(
                    select(OpTarget)
                    .where(OpTarget.bot_id == bot_id)
                    .order_by(OpTarget.position, OpTarget.id)
                )
            ).all()
        )


async def get_target(bot_id: int, target_id: int) -> OpTarget | None:
    async with async_session() as s:
        target = await s.get(OpTarget, target_id)
    if target is None or target.bot_id != bot_id:
        return None
    return target


def parse_chat_ref(raw: str) -> str | int:
    """'@name', '-100...' или ссылка t.me -> аргумент для bot.get_chat."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("Пустое значение.")
    if "t.me/" in value or "telegram.me/" in value:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        name = parsed.path.strip("/").split("/", 1)[0]
        if not name:
            raise ValueError("В ссылке нет имени канала.")
        value = name
    if value.startswith("@"):
        return value
    if value.lstrip("-").isdigit():
        return int(value)
    if value.startswith("https://"):
        raise ValueError("Нужна ссылка t.me, @username или числовой chat_id.")
    return "@" + value


async def resolve_chat(bot, raw: str):
    chat = await bot.get_chat(parse_chat_ref(raw))
    if chat is None:
        raise ValueError("Чат не найден.")
    return chat


async def _bot_rights(bot, chat_id: int) -> tuple[str, str | None]:
    """Права бота в чате: ('verified', None) или ('error', причина)."""
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
    except Exception as error:
        return "error", core._reason(error)
    status = core._status(member)
    if status in {"creator", "administrator"}:
        return "verified", None
    return "error", f"not_admin:{status or 'unknown'}"


def _join_url(chat) -> str:
    username = getattr(chat, "username", None)
    return f"https://t.me/{username}" if username else ""


async def add_target(
    bot, bot_id: int, actor_id: int, chat
) -> tuple[OpTarget, str | None]:
    """Добавляет или обновляет ресурс; возвращает (цель, ошибка прав).

    Права бота проверяются сразу: без администратора getChatMember не
    отдаёт чужие подписки, и такой ресурс честно помечается ошибкой, а не
    притворяется рабочим.
    """
    chat_id = int(chat.id)
    title = getattr(chat, "title", None) or str(chat_id)
    chat_type = getattr(chat, "type", "") or "channel"
    join_url = _join_url(chat)
    setup_status, error = await _bot_rights(bot, chat_id)
    if not join_url and setup_status == "verified":
        try:
            link = await bot.create_chat_invite_link(chat_id, name=INVITE_NAME)
            join_url = getattr(link, "invite_link", "") or ""
        except Exception:
            logger.info("event=op_invite_failed chat_id=%s", chat_id)
    now = utcnow()
    async with async_session.begin() as s:
        target = await s.scalar(
            select(OpTarget).where(
                OpTarget.bot_id == bot_id, OpTarget.chat_id == chat_id
            )
        )
        created = target is None
        if created:
            position = await s.scalar(
                select(func.coalesce(func.max(OpTarget.position), 0)).where(
                    OpTarget.bot_id == bot_id
                )
            )
            target = OpTarget(
                bot_id=bot_id,
                chat_id=chat_id,
                position=(position or 0) + 1,
            )
            s.add(target)
        target.title = title
        target.chat_type = chat_type
        target.join_url = join_url or target.join_url or ""
        target.setup_status = setup_status
        target.last_error = error
        target.last_checked_at = now
        await s.flush()
        await audit(
            s,
            bot_id,
            actor_id,
            "target_add" if created else "target_update",
            {
                "target_id": target.id,
                "chat_id": chat_id,
                "title": title,
                "rights": setup_status,
            },
        )
    return target, error


async def check_target(bot, bot_id: int, target_id: int) -> OpTarget | None:
    async with async_session.begin() as s:
        target = await s.get(OpTarget, target_id)
        if target is None or target.bot_id != bot_id:
            return None
        target.setup_status, target.last_error = await _bot_rights(bot, target.chat_id)
        target.last_checked_at = utcnow()
    return target


async def check_all(bot, bot_id: int) -> dict[str, int]:
    """Проверяет права бота во всех ресурсах; возвращает счётчики."""
    targets = await list_targets(bot_id)
    result = {"ok": 0, "error": 0}
    for target in targets:
        checked = await check_target(bot, bot_id, target.id)
        if checked is not None:
            result["ok" if checked.setup_status == "verified" else "error"] += 1
    return result


async def set_target_active(bot_id: int, actor_id: int, target_id: int, value: bool):
    async with async_session.begin() as s:
        target = await s.get(OpTarget, target_id)
        if target is None or target.bot_id != bot_id:
            return None
        target.is_active = value
        await audit(
            s, bot_id, actor_id, f"target_active:{int(value)}", {"target_id": target_id}
        )
    return target


async def set_join_url(bot_id: int, actor_id: int, target_id: int, url: str):
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if parsed.scheme not in {"https", "http"} or host not in {
        "t.me",
        "telegram.me",
        "telegram.dog",
    }:
        raise ValueError("Нужна ссылка вида https://t.me/...")
    async with async_session.begin() as s:
        target = await s.get(OpTarget, target_id)
        if target is None or target.bot_id != bot_id:
            return None
        target.join_url = url
        await audit(s, bot_id, actor_id, "target_link", {"target_id": target_id})
    return target


async def delete_target(bot_id: int, actor_id: int, target_id: int) -> bool:
    async with async_session.begin() as s:
        target = await s.get(OpTarget, target_id)
        if target is None or target.bot_id != bot_id:
            return False
        s.add(
            OpAuditLog(
                bot_id=bot_id,
                actor_id=actor_id,
                action="target_delete",
                payload={"target_id": target_id, "title": target.title},
            )
        )
        await s.delete(target)
    return True


async def diagnostics(bot_id: int) -> dict:
    data = await overview(bot_id)
    async with async_session() as s:
        errors = list(
            (
                await s.scalars(
                    select(OpErrorLog)
                    .where(OpErrorLog.bot_id == bot_id)
                    .order_by(OpErrorLog.id.desc())
                    .limit(10)
                )
            ).all()
        )
    data["errors"] = errors
    return data


async def audit_page(bot_id: int, limit: int = 20) -> list[OpAuditLog]:
    async with async_session() as s:
        return list(
            (
                await s.scalars(
                    select(OpAuditLog)
                    .where(OpAuditLog.bot_id == bot_id)
                    .order_by(OpAuditLog.id.desc())
                    .limit(limit)
                )
            ).all()
        )


async def schedule_target(
    bot_id: int,
    actor_id: int,
    target_id: int,
    starts_at: datetime | None,
    ends_at: datetime | None,
):
    async with async_session.begin() as s:
        target = await s.get(OpTarget, target_id)
        if target is None or target.bot_id != bot_id:
            return None
        target.starts_at = starts_at
        target.ends_at = ends_at
        await audit(
            s,
            bot_id,
            actor_id,
            "target_window",
            {"target_id": target_id, "starts": str(starts_at), "ends": str(ends_at)},
        )
    return target
