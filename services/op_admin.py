"""ОП v2: панель управления -- данные и проверки, без текстов и кнопок.

Все операции скоупятся по bot_id: конфигурация у каждого бота своя.
Каждое изменение конфигурации пишет запись в op_audit_log; проверки
прав бота -- в op_targets.setup_status/last_error. Тексты экранов и
клавиатуры живут в handlers/op_admin.py и keyboards/inline.py.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select

from models.base import async_session
from models.op import (
    OpAuditLog,
    OpErrorLog,
    OpExemption,
    OpMessageTemplate,
    OpPolicy,
    OpRuleGroup,
    OpRuleTarget,
    OpTarget,
)
from services import op as core
from utils.time import KYIV, utcnow

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
        if field == "enabled" and value:
            await _bootstrap_global(s, bot_id, actor_id)
    return settings


async def _bootstrap_global(s, bot_id: int, actor_id: int) -> None:
    """При включении ОП без правил собирает глобальное правило из ресурсов.

    Иначе включённая ОП молчит: ресурсы есть, а требования нет. Правило
    создаётся один раз; если админ уже настроил политики, состав не трогаем.
    """
    existing = await s.scalar(
        select(OpPolicy.id).where(
            OpPolicy.bot_id == bot_id, OpPolicy.feature_key == "*"
        ).limit(1)
    )
    if existing is not None:
        return
    targets = list(
        (
            await s.scalars(
                select(OpTarget)
                .where(OpTarget.bot_id == bot_id, OpTarget.is_active.is_(True))
                .order_by(OpTarget.position, OpTarget.id)
            )
        ).all()
    )
    if not targets:
        return
    position = await s.scalar(
        select(func.coalesce(func.max(OpPolicy.position), 0)).where(
            OpPolicy.bot_id == bot_id
        )
    )
    policy = OpPolicy(bot_id=bot_id, feature_key="*", position=(position or 0) + 1)
    s.add(policy)
    await s.flush()
    group = OpRuleGroup(policy_id=policy.id, mode="and", position=1)
    s.add(group)
    await s.flush()
    for target in targets:
        s.add(OpRuleTarget(group_id=group.id, target_id=target.id))
    await audit(
        s,
        bot_id,
        actor_id,
        "policy_bootstrap",
        {
            "policy_id": policy.id,
            "group_id": group.id,
            "targets": [target.id for target in targets],
        },
    )


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
        if created:
            await _attach_global(s, bot_id, actor_id, target)
    return target, error


async def _attach_global(s, bot_id: int, actor_id: int, target: OpTarget) -> None:
    """Новый ресурс сразу становится требованием, а не висит без правила.

    Первый ресурс создаёт глобальное правило с одной группой «И». Следующие
    добавляются в ту же группу, пока она единственная и режим «И»: тогда
    «все нужные ресурсы» означают ровно то, что видит админ. Как только он
    собрал «ИЛИ» или несколько групп, автоматика молчит -- состав дальше
    только ручной.
    """
    policies = list(
        (
            await s.scalars(
                select(OpPolicy)
                .where(OpPolicy.bot_id == bot_id, OpPolicy.feature_key == "*")
                .order_by(OpPolicy.position, OpPolicy.id)
            )
        ).all()
    )
    if not policies:
        position = await s.scalar(
            select(func.coalesce(func.max(OpPolicy.position), 0)).where(
                OpPolicy.bot_id == bot_id
            )
        )
        policy = OpPolicy(
            bot_id=bot_id, feature_key="*", position=(position or 0) + 1
        )
        s.add(policy)
        await s.flush()
        group = OpRuleGroup(policy_id=policy.id, mode="and", position=1)
        s.add(group)
        await s.flush()
        s.add(OpRuleTarget(group_id=group.id, target_id=target.id))
        await audit(
            s,
            bot_id,
            actor_id,
            "policy_auto",
            {
                "policy_id": policy.id,
                "group_id": group.id,
                "target_id": target.id,
            },
        )
        return
    if len(policies) != 1:
        return
    groups = list(
        (
            await s.scalars(
                select(OpRuleGroup).where(OpRuleGroup.policy_id == policies[0].id)
            )
        ).all()
    )
    if len(groups) != 1 or groups[0].mode != "and":
        return
    linked = await s.scalar(
        select(OpRuleTarget).where(
            OpRuleTarget.group_id == groups[0].id,
            OpRuleTarget.target_id == target.id,
        )
    )
    if linked is None:
        s.add(OpRuleTarget(group_id=groups[0].id, target_id=target.id))
        await audit(
            s,
            bot_id,
            actor_id,
            "group_target:1",
            {
                "group_id": groups[0].id,
                "target_id": target.id,
                "auto": True,
            },
        )


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
        links = list(
            (
                await s.scalars(
                    select(OpRuleTarget).where(OpRuleTarget.target_id == target_id)
                )
            ).all()
        )
        for link in links:
            await s.delete(link)
        await s.delete(target)
    return True


async def config_issues(data: dict) -> list[str]:
    """Замечания к конфигурации: то, что делает ОП тихой или непроходимой."""
    issues: list[str] = []
    settings = data["settings"]
    now = utcnow()
    live_targets = [
        target
        for target in data["targets"]
        if target.is_active and core.window_live(target.starts_at, target.ends_at, now)
    ]
    live_policies = [
        policy
        for policy in data["policies"]
        if policy.enabled and core.window_live(policy.starts_at, policy.ends_at, now)
    ]
    if settings.enabled and not settings.paused:
        if not live_targets:
            issues.append("ОП включена, но активных ресурсов нет — проверять нечего.")
        if not live_policies:
            issues.append("ОП включена, но включённых правил нет — гейт открыт.")
    for target in data["targets"]:
        name = target.title or target.chat_id
        if target.is_active and not target.join_url:
            issues.append(f"У ресурса «{name}» нет ссылки — пользователь не сможет вступить.")
        if target.is_active and target.setup_status != "verified":
            issues.append(
                f"Ресурс «{name}»: права бота не подтверждены "
                f"({target.last_error or target.setup_status})."
            )
    async with async_session() as s:
        for policy in live_policies:
            title = (
                "Глобальное"
                if policy.feature_key == "*"
                else core.feature_name(policy.feature_key, "ru")
            )
            groups = list(
                (
                    await s.scalars(
                        select(OpRuleGroup)
                        .where(OpRuleGroup.policy_id == policy.id)
                        .order_by(OpRuleGroup.position, OpRuleGroup.id)
                    )
                ).all()
            )
            if not groups:
                issues.append(f"Правило «{title}» без групп — ничего не требует.")
            for index, group in enumerate(groups, 1):
                count = await s.scalar(
                    select(func.count(OpRuleTarget.id)).where(
                        OpRuleTarget.group_id == group.id
                    )
                )
                if not count:
                    issues.append(f"Правило «{title}», группа {index}: ресурсы не отмечены.")
    return issues


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
    data["issues"] = await config_issues(data)
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


# ── Шаблоны сообщений и подписи кнопок ─────────────────────────

FORMATS = ("html", "markdown", "plain")
FORMAT_TITLES = {"html": "HTML", "markdown": "MarkdownV2", "plain": "обычный текст"}
BUTTON_FIELDS = {"join": "btn_join", "check": "btn_check", "back": "btn_back"}
BUTTON_TITLES = {"join": "Вступление", "check": "Проверка", "back": "Назад"}


def _template_stmt(bot_id: int, scope: str, feature_key: str | None):
    stmt = select(OpMessageTemplate).where(
        OpMessageTemplate.bot_id == bot_id,
        OpMessageTemplate.scope == scope,
    )
    if scope == "feature":
        stmt = stmt.where(OpMessageTemplate.feature_key == feature_key)
    else:
        stmt = stmt.where(OpMessageTemplate.feature_key.is_(None))
    return stmt.limit(1)


async def get_template(bot_id: int, scope: str, feature_key: str | None = None):
    async with async_session() as s:
        return await s.scalar(_template_stmt(bot_id, scope, feature_key))


async def list_templates(bot_id: int) -> list[OpMessageTemplate]:
    async with async_session() as s:
        return list(
            (
                await s.scalars(
                    select(OpMessageTemplate)
                    .where(OpMessageTemplate.bot_id == bot_id)
                    .order_by(OpMessageTemplate.id)
                )
            ).all()
        )


async def save_template(
    bot_id: int,
    actor_id: int,
    scope: str,
    feature_key: str | None,
    text: str,
    fmt: str,
):
    """Сохраняет шаблон после валидации; неизвестные переменные не пройдут."""
    if fmt not in FORMATS:
        fmt = "html"
    if scope not in {"global", "error", "feature"}:
        raise ValueError("Неизвестный тип шаблона.")
    if scope == "feature" and not feature_key:
        raise ValueError("Не указана функция.")
    problem = core.validate_template(text, fmt)
    if problem:
        raise ValueError(problem)
    async with async_session.begin() as s:
        row = await s.scalar(_template_stmt(bot_id, scope, feature_key))
        if row is None:
            row = OpMessageTemplate(
                bot_id=bot_id,
                scope=scope,
                feature_key=feature_key if scope == "feature" else None,
            )
            s.add(row)
        row.text = text
        row.format = fmt
        row.updated_at = utcnow()
        await s.flush()
        await audit(
            s,
            bot_id,
            actor_id,
            "template_save",
            {"scope": scope, "feature": feature_key, "format": fmt},
        )
    return row


async def set_template_format(
    bot_id: int, actor_id: int, scope: str, feature_key: str | None, fmt: str
):
    if fmt not in FORMATS:
        raise ValueError("Неизвестный формат.")
    async with async_session.begin() as s:
        row = await s.scalar(_template_stmt(bot_id, scope, feature_key))
        if row is None:
            return None
        row.format = fmt
        row.updated_at = utcnow()
        await audit(
            s,
            bot_id,
            actor_id,
            "template_format",
            {"scope": scope, "feature": feature_key, "format": fmt},
        )
    return row


async def delete_template(
    bot_id: int, actor_id: int, scope: str, feature_key: str | None
) -> bool:
    async with async_session.begin() as s:
        row = await s.scalar(_template_stmt(bot_id, scope, feature_key))
        if row is None:
            return False
        await s.delete(row)
        await audit(
            s,
            bot_id,
            actor_id,
            "template_delete",
            {"scope": scope, "feature": feature_key},
        )
    return True


async def feature_keys(bot_id: int) -> list[str]:
    """Ключи функций: встроенные + из правил + из готовых шаблонов."""
    async with async_session() as s:
        policy_keys = set(
            (
                await s.scalars(
                    select(OpPolicy.feature_key).where(
                        OpPolicy.bot_id == bot_id, OpPolicy.feature_key != "*"
                    )
                )
            ).all()
        )
        template_keys = set(
            (
                await s.scalars(
                    select(OpMessageTemplate.feature_key).where(
                        OpMessageTemplate.bot_id == bot_id,
                        OpMessageTemplate.scope == "feature",
                        OpMessageTemplate.feature_key.is_not(None),
                    )
                )
            ).all()
        )
    return sorted((set(core.FEATURE_NAMES) - {"*"}) | policy_keys | template_keys)


async def get_settings(bot_id: int):
    async with async_session.begin() as s:
        return await core.settings_or_create(s, bot_id)


async def set_button_label(bot_id: int, actor_id: int, field: str, value: str | None):
    column = BUTTON_FIELDS.get(field)
    if column is None:
        raise ValueError("Неизвестная кнопка.")
    if value is not None:
        value = value.strip()
        if len(value) > 64:
            raise ValueError("Подпись длиннее 64 символов — Telegram не примет.")
        value = value or None
    async with async_session.begin() as s:
        settings = await core.settings_or_create(s, bot_id)
        setattr(settings, column, value)
        settings.updated_at = utcnow()
        await audit(
            s,
            bot_id,
            actor_id,
            "button_label",
            {"field": field, "value": value},
        )
    return settings


# ── Правила: политики, группы, состав ──────────────────────────

async def list_policies(bot_id: int) -> list[OpPolicy]:
    async with async_session() as s:
        return list(
            (
                await s.scalars(
                    select(OpPolicy)
                    .where(OpPolicy.bot_id == bot_id)
                    .order_by(OpPolicy.position, OpPolicy.id)
                )
            ).all()
        )


async def get_policy(bot_id: int, policy_id: int) -> OpPolicy | None:
    async with async_session() as s:
        policy = await s.get(OpPolicy, policy_id)
    if policy is None or policy.bot_id != bot_id:
        return None
    return policy


async def create_policy(bot_id: int, actor_id: int, feature_key: str) -> OpPolicy:
    async with async_session.begin() as s:
        position = await s.scalar(
            select(func.coalesce(func.max(OpPolicy.position), 0)).where(
                OpPolicy.bot_id == bot_id
            )
        )
        policy = OpPolicy(
            bot_id=bot_id,
            feature_key=feature_key or "*",
            position=(position or 0) + 1,
        )
        s.add(policy)
        await s.flush()
        await audit(
            s,
            bot_id,
            actor_id,
            "policy_create",
            {"policy_id": policy.id, "feature_key": policy.feature_key},
        )
    return policy


async def set_policy_enabled(
    bot_id: int, actor_id: int, policy_id: int, value: bool
) -> OpPolicy | None:
    async with async_session.begin() as s:
        policy = await s.get(OpPolicy, policy_id)
        if policy is None or policy.bot_id != bot_id:
            return None
        policy.enabled = value
        await audit(
            s,
            bot_id,
            actor_id,
            f"policy_enabled:{int(value)}",
            {"policy_id": policy_id},
        )
    return policy


async def delete_policy(bot_id: int, actor_id: int, policy_id: int) -> bool:
    async with async_session.begin() as s:
        policy = await s.get(OpPolicy, policy_id)
        if policy is None or policy.bot_id != bot_id:
            return False
        groups = list(
            (
                await s.scalars(
                    select(OpRuleGroup).where(OpRuleGroup.policy_id == policy_id)
                )
            ).all()
        )
        for group in groups:
            links = list(
                (
                    await s.scalars(
                        select(OpRuleTarget).where(OpRuleTarget.group_id == group.id)
                    )
                ).all()
            )
            for link in links:
                await s.delete(link)
            await s.delete(group)
        await s.delete(policy)
        await audit(
            s,
            bot_id,
            actor_id,
            "policy_delete",
            {"policy_id": policy_id, "feature_key": policy.feature_key},
        )
    return True


async def list_groups(policy_id: int) -> list[OpRuleGroup]:
    async with async_session() as s:
        return list(
            (
                await s.scalars(
                    select(OpRuleGroup)
                    .where(OpRuleGroup.policy_id == policy_id)
                    .order_by(OpRuleGroup.position, OpRuleGroup.id)
                )
            ).all()
        )


async def group_counts(policy_id: int) -> dict[int, int]:
    async with async_session() as s:
        rows = (
            await s.execute(
                select(OpRuleTarget.group_id, func.count(OpRuleTarget.id))
                .join(OpRuleGroup, OpRuleGroup.id == OpRuleTarget.group_id)
                .where(OpRuleGroup.policy_id == policy_id)
                .group_by(OpRuleTarget.group_id)
            )
        ).all()
    return {group_id: count for group_id, count in rows}


async def get_group(bot_id: int, group_id: int):
    async with async_session() as s:
        group = await s.get(OpRuleGroup, group_id)
        if group is None:
            return None
        policy = await s.get(OpPolicy, group.policy_id)
        if policy is None or policy.bot_id != bot_id:
            return None
    return group, policy


async def add_group(
    bot_id: int, actor_id: int, policy_id: int, mode: str
) -> OpRuleGroup | None:
    if mode not in {"and", "or"}:
        raise ValueError("Режим группы — and или or.")
    async with async_session.begin() as s:
        policy = await s.get(OpPolicy, policy_id)
        if policy is None or policy.bot_id != bot_id:
            return None
        position = await s.scalar(
            select(func.coalesce(func.max(OpRuleGroup.position), 0)).where(
                OpRuleGroup.policy_id == policy_id
            )
        )
        group = OpRuleGroup(
            policy_id=policy_id, mode=mode, position=(position or 0) + 1
        )
        s.add(group)
        await s.flush()
        await audit(
            s,
            bot_id,
            actor_id,
            "group_create",
            {"policy_id": policy_id, "group_id": group.id, "mode": mode},
        )
    return group


async def delete_group(bot_id: int, actor_id: int, group_id: int) -> bool:
    async with async_session.begin() as s:
        group = await s.get(OpRuleGroup, group_id)
        if group is None:
            return False
        policy = await s.get(OpPolicy, group.policy_id)
        if policy is None or policy.bot_id != bot_id:
            return False
        links = list(
            (
                await s.scalars(
                    select(OpRuleTarget).where(OpRuleTarget.group_id == group_id)
                )
            ).all()
        )
        for link in links:
            await s.delete(link)
        await s.delete(group)
        await audit(
            s,
            bot_id,
            actor_id,
            "group_delete",
            {"policy_id": policy.id, "group_id": group_id},
        )
    return True


async def group_target_ids(group_id: int) -> set[int]:
    async with async_session() as s:
        return set(
            (
                await s.scalars(
                    select(OpRuleTarget.target_id).where(
                        OpRuleTarget.group_id == group_id
                    )
                )
            ).all()
        )


async def toggle_group_target(
    bot_id: int, actor_id: int, group_id: int, target_id: int
) -> bool | None:
    """Добавляет ресурс в группу или убирает его; возвращает новое состояние."""
    async with async_session.begin() as s:
        group = await s.get(OpRuleGroup, group_id)
        if group is None:
            return None
        policy = await s.get(OpPolicy, group.policy_id)
        target = await s.get(OpTarget, target_id)
        if (
            policy is None
            or policy.bot_id != bot_id
            or target is None
            or target.bot_id != bot_id
        ):
            return None
        link = await s.scalar(
            select(OpRuleTarget).where(
                OpRuleTarget.group_id == group_id,
                OpRuleTarget.target_id == target_id,
            )
        )
        if link is None:
            s.add(OpRuleTarget(group_id=group_id, target_id=target_id))
            linked = True
        else:
            await s.delete(link)
            linked = False
        await audit(
            s,
            bot_id,
            actor_id,
            f"group_target:{int(linked)}",
            {"group_id": group_id, "target_id": target_id},
        )
    return linked


# ── Расписание: разбор окна и запись ───────────────────────────

def _moment(value: str):
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            naive = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=KYIV).astimezone(timezone.utc)
    raise ValueError(f"Не понял дату «{value}». Формат: ДД.ММ.ГГГГ ЧЧ:ММ.")


def parse_window(raw: str):
    """'01.11.2026 09:00 — 07.11.2026 23:00', открытые концы или 'сброс'."""
    value = (raw or "").strip()
    if value.lower() in {"сброс", "reset", "-", "—", "–", "нет"}:
        return None, None
    parts = re.split(r"\s*(?:—|–|\s-\s|\bдо\b)\s*", value, maxsplit=1)
    if len(parts) != 2:
        raise ValueError(
            "Нужны две даты через «—», например: 01.11.2026 09:00 — 07.11.2026 23:00."
        )
    starts_at = _moment(parts[0])
    ends_at = _moment(parts[1])
    if starts_at is None and ends_at is None:
        raise ValueError("Обе границы пустые. Для снятия окна отправь «сброс».")
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise ValueError("Конец окна должен быть позже начала.")
    return starts_at, ends_at


async def schedule_settings(bot_id: int, actor_id: int, starts_at, ends_at):
    async with async_session.begin() as s:
        settings = await core.settings_or_create(s, bot_id)
        settings.starts_at = starts_at
        settings.ends_at = ends_at
        settings.updated_at = utcnow()
        await audit(
            s,
            bot_id,
            actor_id,
            "settings_window",
            {"starts": str(starts_at), "ends": str(ends_at)},
        )
    return settings


async def schedule_policy(bot_id: int, actor_id: int, policy_id: int, starts_at, ends_at):
    async with async_session.begin() as s:
        policy = await s.get(OpPolicy, policy_id)
        if policy is None or policy.bot_id != bot_id:
            return None
        policy.starts_at = starts_at
        policy.ends_at = ends_at
        await audit(
            s,
            bot_id,
            actor_id,
            "policy_window",
            {"policy_id": policy_id, "starts": str(starts_at), "ends": str(ends_at)},
        )
    return policy


async def set_target_window(
    bot_id: int, actor_id: int, target_id: int, starts_at, ends_at
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


# ── Исключения ─────────────────────────────────────────────────

async def list_exemptions(bot_id: int) -> list[OpExemption]:
    async with async_session() as s:
        return list(
            (
                await s.scalars(
                    select(OpExemption)
                    .where(OpExemption.bot_id == bot_id)
                    .order_by(OpExemption.id)
                )
            ).all()
        )


async def add_exemption(
    bot_id: int, actor_id: int, user_id: int, until, note: str = ""
):
    async with async_session.begin() as s:
        row = await s.scalar(
            select(OpExemption).where(
                OpExemption.bot_id == bot_id, OpExemption.user_id == user_id
            )
        )
        if row is None:
            row = OpExemption(bot_id=bot_id, user_id=user_id)
            s.add(row)
        row.until = until
        row.note = note
        row.created_by = actor_id
        await audit(
            s,
            bot_id,
            actor_id,
            "exemption_add",
            {"user_id": user_id, "until": str(until)},
        )
    return row


async def delete_exemption(bot_id: int, actor_id: int, user_id: int) -> bool:
    async with async_session.begin() as s:
        row = await s.scalar(
            select(OpExemption).where(
                OpExemption.bot_id == bot_id, OpExemption.user_id == user_id
            )
        )
        if row is None:
            return False
        await s.delete(row)
        await audit(s, bot_id, actor_id, "exemption_delete", {"user_id": user_id})
    return True
