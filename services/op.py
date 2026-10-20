"""Обязательная подписка (ОП) v2 — ядро по ТЗ владельца.

Модуль обслуживает оба бота: конфигурация лежит по bot_id, а проверка идёт
тем ботом, который доставил апдейт -- Telegram отдаёт чужие подписки через
getChatMember только администратору чата.

Инварианты:
- Активность вычисляется, а не хранится: требование действует, пока тумблер
  включён и есть живые ресурсы. Окна (общее, ресурса, правила) проверяются
  на каждом запросе, поэтому рестарт не может оставить просроченную ОП
  включённой.
- Ошибка Telegram/прав никогда не считается подтверждением подписки:
  пользователь видит техническое сообщение, доступ не открывается.
- Правило = набор групп; группа 'and' требует все ресурсы, 'or' -- любой.
  Глобальное правило ('*') действует всегда, правило функции добавляется
  поверх него.
- Проверка строгая: результат не кешируется между действиями (ТЗ, раздел 8).
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import or_, select

import config
from models.base import async_session
from models.op import (
    OpErrorLog,
    OpExemption,
    OpMessageTemplate,
    OpPolicy,
    OpRuleGroup,
    OpRuleTarget,
    OpSettings,
    OpTarget,
)
from utils import bots
from utils.time import utcnow

logger = logging.getLogger(__name__)

# Писать last_checked_at на каждую проверку -- лишняя нагрузка: панели
# достаточно свежести в пять минут, ошибки пишутся всегда.
TOUCH_TTL = 300
# Полный экран на серые тапы -- не чаще раза в полторы минуты.
WARN_TTL = 90

OK_STATUSES = {"creator", "administrator", "member"}

SCREEN_TITLE = {
    "ru": "🔒 Доступ временно ограничен",
    "en": "🔒 Access is temporarily restricted",
    "uk": "🔒 Доступ тимчасово обмежено",
}
SCREEN_BODY = {
    "ru": "Для продолжения работы с ботом выполни условия обязательной подписки.\n\nПодпишись на каналы и группы ниже, потом нажми «Проверить подписку».",
    "en": "To keep using the bot, meet the mandatory subscription requirements.\n\nSubscribe to the channels and groups below, then tap “Check subscription”.",
    "uk": "Щоб продовжити роботу з ботом, виконай умови обов’язкової підписки.\n\nПідпишись на канали й групи нижче, потім натисни «Перевірити підписку».",
}
MISSING = {
    "ru": "Не хватает: {titles}",
    "en": "Still missing: {titles}",
    "uk": "Бракує: {titles}",
}
ERROR_TEXT = {
    "ru": "⚠️ Telegram не ответил, проверить подписку сейчас не вышло. Доступ не открыт — попробуй ещё раз через минуту.",
    "en": "⚠️ Telegram did not respond, the subscription check failed. Access stays closed — try again in a minute.",
    "uk": "⚠️ Telegram не відповів, перевірити підписку зараз не вдалося. Доступ не відкрито — спробуй ще раз за хвилину.",
}
CHECK_BUTTON = {
    "ru": "✅ Проверить подписку",
    "en": "✅ Check subscription",
    "uk": "✅ Перевірити підписку",
}
BACK_BUTTON = {
    "ru": "← Назад в меню",
    "en": "← Back to menu",
    "uk": "← Назад до меню",
}
JOIN_BUTTON = {
    "ru": "Подписаться",
    "en": "Subscribe",
    "uk": "Підписатися",
}
OK_TEXT = {
    "ru": "✅ Подписка подтверждена — доступ открыт.",
    "en": "✅ Subscription confirmed — access granted.",
    "uk": "✅ Підписку підтверджено — доступ відкрито.",
}
OFF_TEXT = {
    "ru": "Ограничение снято — можно пользоваться ботом.",
    "en": "The restriction is lifted — you can use the bot.",
    "uk": "Обмеження знято — можна користуватися ботом.",
}
ALERT = {
    "ru": "Сначала подпишись на каналы из сообщения 👇",
    "en": "Subscribe to the channels in the message first 👇",
    "uk": "Спершу підпишись на канали з повідомлення 👇",
}

SCREEN_STATUS_OK = "✅"
SCREEN_STATUS_MISSING = "⛔"

# Действия, которые обязаны работать при закрытом гейте: регистрация,
# справка/правила, экран ОП и перепроверка, владельческие команды, платежи
# (деньги не должны упираться в подписку) и служебные callback'и.
ALLOWED_COMMANDS = {
    "/start",
    "/help",
    "/rules",
    "/language",
    "/paysupport",
    "/id",
    "/op",
    "/setup",
    "/unsetup",
    "/admin",
    "/user",
    "/ban_user",
    "/unban",
    "/mute_user",
    "/unmute",
    "/grant",
    "/price",
    "/setting",
    "/season",
}
ALLOWED_PREFIXES = ("op:", "lang:", "admin:", "bc:", "pay:", "v1:buy:", "premium:buy:")
# «Назад» с экрана требования возвращает в меню, но не открывает функции.
ALLOWED_ACTIONS = {"menu"}

FEATURE_NAMES = {
    "*": {"ru": "бот", "en": "the bot", "uk": "бот"},
    "search": {
        "ru": "поиск собеседника",
        "en": "chat search",
        "uk": "пошук співрозмовника",
    },
    "chat": {"ru": "чат", "en": "chat", "uk": "чат"},
    "profile": {"ru": "профиль", "en": "profile", "uk": "профіль"},
    "premium": {"ru": "Premium", "en": "Premium", "uk": "Premium"},
    "stats": {"ru": "статистику", "en": "stats", "uk": "статистику"},
}

VARIABLES = {"first_name", "username", "bot_name", "missing_count", "function_name"}
_VAR = re.compile(r"\{([a-z_]+)\}")
_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code",
    "pre", "blockquote", "span", "tg-spoiler", "br",
}
_MD_SPECIAL = set("_*[]()~`>#+-=|{}.!")


def tr(table, lang):
    return table.get(lang) or table["ru"]


def fmt_when(moment) -> str:
    from utils.time import KYIV

    if moment is None:
        return "—"
    if moment.tzinfo is None:
        from datetime import timezone

        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KYIV).strftime("%d.%m.%Y %H:%M")


def _status(member) -> str:
    status = getattr(member, "status", "")
    return getattr(status, "value", status) or ""


def _reason(error: Exception) -> str:
    if isinstance(error, TelegramForbiddenError):
        return "forbidden"
    if isinstance(error, TelegramBadRequest):
        return "bad_request"
    if isinstance(error, TelegramRetryAfter):
        return "retry_after"
    return type(error).__name__[:64]


def _window_live(starts_at, ends_at, now: datetime) -> bool:
    # SQLite отдаёт naive datetime; прод-Postgres -- aware. Считаем naive UTC,
    # иначе сравнение падает с TypeError в тестах и на sqlite-стендах.
    if starts_at is not None and starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=now.tzinfo)
    if ends_at is not None and ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=now.tzinfo)
    if starts_at is not None and starts_at > now:
        return False
    if ends_at is not None and ends_at <= now:
        return False
    return True


# Панель считает окна той же функцией, что и гейт, -- одна логика на всех.
window_live = _window_live


def settings_live(settings: OpSettings | None, now: datetime | None = None) -> bool:
    """Тумблер, пауза и общее окно -- всё сразу.

    Пауза держит конфигурацию, но снимает требование: владелец может
    выключить ОП на время, не теряя ресурсы и правила.
    """
    if settings is None or not settings.enabled or settings.paused:
        return False
    return _window_live(settings.starts_at, settings.ends_at, now or utcnow())


async def settings_for(s, bot_id):
    if bot_id is None:
        return None
    return await s.get(OpSettings, bot_id)


async def settings_or_create(s, bot_id) -> OpSettings:
    settings = await settings_for(s, bot_id)
    if settings is None:
        settings = OpSettings(bot_id=bot_id)
        s.add(settings)
        await s.flush()
    return settings


def gate_applies(command: str, action: str) -> bool:
    """Нужно ли проверять это действие. Разрешённые маршруты -- по ТЗ §2."""
    if command in ALLOWED_COMMANDS:
        return False
    if action in ALLOWED_ACTIONS:
        return False
    return not (action or "").startswith(ALLOWED_PREFIXES)


def feature_for(command: str, action: str) -> str:
    """Ключ функции для правил: команда, префикс callback'а или '*'.

    Reply-кнопки не несут машинного префикса -- для них действует глобальное
    правило; точечные правила задаются командами и callback-префиксами.
    """
    if command:
        return command.lstrip("/").split("@", 1)[0] or "*"
    action = action or ""
    if ":" in action:
        return action.split(":", 1)[0] or "*"
    return "*"


def feature_name(feature_key: str, lang: str) -> str:
    table = FEATURE_NAMES.get(feature_key) or {}
    return table.get(lang) or feature_key


def alert(lang: str) -> str:
    return tr(ALERT, lang)


def button_default(field: str, lang: str = "ru") -> str:
    table = {"join": JOIN_BUTTON, "check": CHECK_BUTTON, "back": BACK_BUTTON}[field]
    return tr(table, lang)


def default_text(scope: str, lang: str = "ru") -> str:
    if scope == "error":
        return tr(ERROR_TEXT, lang)
    return tr(SCREEN_TITLE, lang) + "\n\n" + tr(SCREEN_BODY, lang)


def _escape_md(value: str) -> str:
    return "".join("\\" + char if char in _MD_SPECIAL else char for char in value)


def render(text: str, context: dict, fmt: str = "html") -> str:
    """Подстановка разрешённых переменных с экранированием по формату."""

    def substitute(match):
        name = match.group(1)
        if name not in VARIABLES:
            return match.group(0)
        value = str(context.get(name, ""))
        if fmt == "html":
            return html.escape(value)
        if fmt in ("markdown", "md", "markdownv2"):
            return _escape_md(value)
        return value

    return _VAR.sub(substitute, text or "")


def validate_template(text: str, fmt: str = "html") -> str | None:
    """Проверка шаблона до сохранения: неизвестные переменные и грубый HTML."""
    unknown = sorted(
        {name for name in _VAR.findall(text or "") if name not in VARIABLES}
    )
    if unknown:
        return "Неизвестные переменные: " + ", ".join(
            "{" + name + "}" for name in unknown
        )
    if fmt != "html":
        return None
    stack: list[str] = []
    for closing, raw_name in _TAG.findall(text or ""):
        name = raw_name.lower()
        if name not in ALLOWED_TAGS:
            return f"Тег <{name}> Telegram не поддерживает."
        if name == "br":
            continue
        if closing:
            if not stack or stack[-1] != name:
                return f"Закрывающий тег </{name}> без открывающего."
            stack.pop()
        else:
            stack.append(name)
    if stack:
        return f"Тег <{stack[-1]}> не закрыт."
    return None


@dataclass
class Gate:
    """Результат проверки для одного пользователя.

    blocked=True значит: требование активно и пользователь его ещё не прошёл.
    Ошибка проверки тоже закрывает доступ: «не смогли проверить» никогда не
    равно «подписан» (ТЗ §9).
    """

    required: bool = False
    bot_id: int | None = None
    feature_key: str = "*"
    settings: OpSettings | None = None
    targets: list[OpTarget] = field(default_factory=list)
    missing_ids: set[int] = field(default_factory=set)
    error_detail: str | None = None
    template: str | None = None
    template_format: str = "html"
    error_template: str | None = None
    error_format: str = "html"
    first_name: str = ""
    username: str | None = None

    @property
    def blocked(self) -> bool:
        return self.required and (bool(self.missing_ids) or bool(self.error_detail))


async def warn_allowed(bot_id, user_id) -> bool:
    """Троттлинг полного экрана на серые тапы: раз в WARN_TTL секунд."""
    try:
        from services.chat_manager import get_redis

        return bool(
            await (await get_redis()).set(
                f"op:warn:{bot_id}:{user_id}", "1", ex=WARN_TTL, nx=True
            )
        )
    except Exception:
        return True


async def chat_membership(bot, chat_id, user_id) -> tuple[str, str]:
    """('ok'|'no'|'error', деталь).

    Статусы по ТЗ §9: creator/administrator/member -- есть; restricted --
    только с is_member=true; left/kicked -- нет. Любая ошибка Telegram или
    потеря прав -- 'error', и это подтверждением не считается.
    """
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramRetryAfter as error:
        return "error", f"retry_after:{error.retry_after}"
    except Exception as error:
        logger.warning(
            "event=op_check_failed chat_id=%s error=%s", chat_id, type(error).__name__
        )
        return "error", _reason(error)
    status = _status(member)
    if status in OK_STATUSES:
        return "ok", status
    if status == "restricted" and getattr(member, "is_member", False):
        return "ok", status
    return "no", status


async def has_exemption(s, bot_id, user_id, now: datetime) -> bool:
    found = await s.scalar(
        select(OpExemption.id).where(
            OpExemption.bot_id == bot_id,
            OpExemption.user_id == user_id,
            or_(OpExemption.until.is_(None), OpExemption.until > now),
        )
    )
    return found is not None


async def _template(s, bot_id, scope: str, feature_key: str | None = None):
    """(текст, формат) шаблона или (None, 'html').

    Приоритет по ТЗ §10: шаблон функции, если задан, иначе глобальный
    (для scope='error' -- шаблон технической ошибки).
    """
    if feature_key and feature_key != "*":
        row = await s.scalar(
            select(OpMessageTemplate)
            .where(
                OpMessageTemplate.bot_id == bot_id,
                OpMessageTemplate.scope == "feature",
                OpMessageTemplate.feature_key == feature_key,
            )
            .limit(1)
        )
        if row is not None:
            return row.text, row.format or "html"
    row = await s.scalar(
        select(OpMessageTemplate)
        .where(
            OpMessageTemplate.bot_id == bot_id,
            OpMessageTemplate.scope == scope,
            OpMessageTemplate.feature_key.is_(None),
        )
        .limit(1)
    )
    if row is None:
        return None, "html"
    return row.text, row.format or "html"


async def _groups(s, policies, now: datetime) -> list[tuple[str, list[OpTarget]]]:
    """Группы всех действующих правил с живыми ресурсами."""
    groups: list[tuple[str, list[OpTarget]]] = []
    for policy in policies:
        rows = list(
            (
                await s.scalars(
                    select(OpRuleGroup)
                    .where(OpRuleGroup.policy_id == policy.id)
                    .order_by(OpRuleGroup.position, OpRuleGroup.id)
                )
            ).all()
        )
        for group in rows:
            targets = list(
                (
                    await s.scalars(
                        select(OpTarget)
                        .join(OpRuleTarget, OpRuleTarget.target_id == OpTarget.id)
                        .where(
                            OpRuleTarget.group_id == group.id,
                            OpTarget.is_active.is_(True),
                        )
                        .order_by(OpTarget.position, OpTarget.id)
                    )
                ).all()
            )
            live = [
                target
                for target in targets
                if _window_live(target.starts_at, target.ends_at, now)
            ]
            groups.append((group.mode or "and", live))
    return groups


async def _record_checks(bot_id, user_id, checks, now: datetime) -> None:
    """Обновляет состояние ресурсов, пишет журнал ошибок и алертит владельца."""
    try:
        async with async_session.begin() as s:
            for target, state, detail in checks:
                # Объекты пришли из закрытой read-сессии: в write-сессию их
                # надо вернуть через merge, иначе правки уйдут в detached-копию.
                row = await s.merge(target)
                if state == "error":
                    s.add(
                        OpErrorLog(
                            bot_id=bot_id,
                            target_id=row.id,
                            user_id=user_id,
                            kind="check",
                            detail=(detail or "error")[:255],
                        )
                    )
                    row.setup_status = "error"
                    row.last_error = (detail or "error")[:255]
                    row.last_checked_at = now
                    await _alert_owner(s, bot_id, row, detail or "error", now)
                    continue
                fresh = (
                    row.last_checked_at is None
                    or (now - row.last_checked_at) >= timedelta(seconds=TOUCH_TTL)
                    or row.setup_status != "verified"
                    or row.last_error is not None
                )
                if fresh:
                    row.setup_status = "verified"
                    row.last_error = None
                    row.last_checked_at = now
    except Exception:
        logger.warning("event=op_record_failed bot_id=%s", bot_id, exc_info=True)


async def _alert_owner(s, bot_id, target: OpTarget, detail: str, now: datetime) -> None:
    """Владельцу -- один алерт на ресурс и час, без спама на каждой проверке.

    Текст уходит в общую очередь уведомлений; неизвестные типы воркер
    отправляет как обычный текст с экранированием.
    """
    owner = config.OWNER_ID
    if not owner:
        return
    key = f"op_alert:{bot_id}:{target.id}:{detail}:{now:%Y%m%d%H}"
    title = target.title or str(target.chat_id)
    text = (
        f"⚠️ ОП: проверка «{title}» не удалась ({detail}).\n"
        "Проверь права бота в чате — без администратора Telegram не отдаёт подписки."
    )
    try:
        from services.notifications import enqueue

        await enqueue(s, owner, "op_alert", key, text, bot_id=bot_id)
    except Exception:
        logger.warning("event=op_alert_failed bot_id=%s", bot_id, exc_info=True)


async def authorize(bot, actor, feature_key: str = "*") -> Gate:
    """Центральный шлюз: решает, можно ли пользователю это действие.

    Проверка идёт на том боте, который доставил апдейт; конфигурация и
    правила читаются из его строк. Владельцы (ADMIN_IDS) и пользователи с
    исключением проходят без обращения к Telegram.
    """
    bot_id = getattr(bot, "id", None)
    first_name = getattr(actor, "first_name", "") or ""
    username = getattr(actor, "username", None)
    user_id = getattr(actor, "id", None)
    gate = Gate(
        bot_id=bot_id,
        feature_key=feature_key,
        first_name=first_name,
        username=username,
    )
    if bot_id is None or user_id is None:
        return gate
    if user_id in config.ADMIN_IDS:
        return gate
    now = utcnow()
    try:
        async with async_session() as s:
            settings = await settings_for(s, bot_id)
            if not settings_live(settings, now):
                return gate
            gate.settings = settings
            gate.template, gate.template_format = await _template(
                s, bot_id, "global", feature_key
            )
            gate.error_template, gate.error_format = await _template(s, bot_id, "error")
            if await has_exemption(s, bot_id, user_id, now):
                return gate
            policies = list(
                (
                    await s.scalars(
                        select(OpPolicy)
                        .where(
                            OpPolicy.bot_id == bot_id,
                            OpPolicy.enabled.is_(True),
                            OpPolicy.feature_key.in_(("*", feature_key)),
                        )
                        .order_by(OpPolicy.position, OpPolicy.id)
                    )
                ).all()
            )
            policies = [
                policy
                for policy in policies
                if _window_live(policy.starts_at, policy.ends_at, now)
            ]
            if not policies:
                return gate
            groups = await _groups(s, policies, now)
    except Exception:
        # Недоступная БД -- не повод запирать бота: пусть работает, а ошибка
        # уже видна вотчдогами и логами.
        logger.warning("event=op_settings_unavailable bot_id=%s", bot_id, exc_info=True)
        return gate

    unique: dict[int, OpTarget] = {}
    for _mode, targets in groups:
        for target in targets:
            unique.setdefault(target.id, target)
    if not unique:
        # Все ресурсы выключены или просрочены -- требовать нечего.
        return gate

    gate.required = True
    gate.targets = list(unique.values())
    checks = []
    for target in gate.targets:
        state, detail = await chat_membership(bot, target.chat_id, user_id)
        checks.append((target, state, detail))
    await _record_checks(bot_id, user_id, checks, now)

    errors = [(target, detail) for target, state, detail in checks if state == "error"]
    if errors:
        gate.error_detail = ", ".join(detail for _target, detail in errors)
        return gate

    states = {target.id: state for target, state, _detail in checks}
    missing: set[int] = set()
    for mode, targets in groups:
        if not targets:
            continue
        present = [states.get(target.id) == "ok" for target in targets]
        if mode == "or":
            if not any(present):
                missing.update(target.id for target in targets)
        else:
            missing.update(target.id for target, ok in zip(targets, present) if not ok)
    gate.missing_ids = missing
    return gate


def _title(target: OpTarget) -> str:
    return target.title or str(target.chat_id)


def _context(gate: Gate, lang: str) -> dict:
    return {
        "first_name": gate.first_name or "",
        "username": f"@{gate.username}" if gate.username else "",
        "bot_name": bots.label(gate.bot_id) if gate.bot_id else "",
        "missing_count": len(gate.missing_ids),
        "function_name": feature_name(gate.feature_key, lang),
    }


def screen_text(gate: Gate, lang: str) -> str:
    """Текст экрана: ошибка, кастомный шаблон или встроенный со статусами."""
    context = _context(gate, lang)
    if gate.error_detail:
        text = gate.error_template or tr(ERROR_TEXT, lang)
        return render(text, context, gate.error_format)
    missing = [target for target in gate.targets if target.id in gate.missing_ids]
    titles = ", ".join(_title(target) for target in missing)
    if gate.template:
        text = render(gate.template, context, gate.template_format)
        if titles:
            text += "\n\n" + tr(MISSING, lang).format(titles=titles)
        return text
    lines = [tr(SCREEN_TITLE, lang), "", tr(SCREEN_BODY, lang), ""]
    for target in gate.targets:
        mark = (
            SCREEN_STATUS_MISSING if target.id in gate.missing_ids else SCREEN_STATUS_OK
        )
        lines.append(f"{mark} {_title(target)}")
    if titles:
        lines += ["", tr(MISSING, lang).format(titles=titles)]
    return "\n".join(lines)


def screen_kb(gate: Gate, lang: str) -> InlineKeyboardMarkup:
    """Кнопки вступления по недостающим ресурсам и обязательная проверка.

    При технической ошибке кнопок вступления нет: причина не в подписке,
    а в доступе бота к чату.
    """
    rows = []
    if not gate.error_detail:
        join_label = (
            gate.settings.btn_join
            if gate.settings is not None and gate.settings.btn_join
            else tr(JOIN_BUTTON, lang)
        )
        for target in gate.targets:
            if target.id in gate.missing_ids and target.join_url:
                label = f"{join_label}: {_title(target)}"[:64]
                rows.append([InlineKeyboardButton(text=label, url=target.join_url)])
    check_label = (
        gate.settings.btn_check
        if gate.settings is not None and gate.settings.btn_check
        else tr(CHECK_BUTTON, lang)
    )
    rows.append([InlineKeyboardButton(text=check_label[:64], callback_data="op:check")])
    back_label = (
        gate.settings.btn_back
        if gate.settings is not None and gate.settings.btn_back
        else tr(BACK_BUTTON, lang)
    )
    rows.append([InlineKeyboardButton(text=back_label[:64], callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
