"""Обязательная подписка (ОП) -- ядро.

Модуль обслуживает оба бота: конфигурация лежит строкой на bot_id, а
проверка идёт тем ботом, который доставил апдейт -- Telegram отдаёт
чужие подписки через getChatMember только администратору чата, и права
проверяются отдельно для каждого бота.

Инварианты:
- Активность вычисляется, а не хранится: требование действует, пока
  тумблер включён и есть хотя бы один живой ресурс. Просроченный ресурс
  не блокирует даже сразу после рестарта, до всякого планировщика.
- Ошибка Telegram/прав никогда не считается подтверждением подписки:
  пользователь видит «повтори проверку», доступ не открывается.
- Уведомления живут в общей очереди notification_log (тип op_notice) и
  дедуплицируются в пределах кампании: повторный запуск рассылки не
  создаёт дубликатов.
"""

from __future__ import annotations

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
from sqlalchemy import func, or_, select, update

from models.base import async_session
from models.op import OpChat, OpSettings
from models.user import User
from utils.time import KYIV, utcnow

logger = logging.getLogger(__name__)

CACHE_TTL = 60
WARN_TTL = 90
NOTIFY_BATCH = 12
NOTIFY_PAUSE = 0.2

SCREEN = {
    "ru": "📛 Чтобы пользоваться ботом, подпишись на каналы и группы ниже — потом нажми «Проверить подписку».",
    "en": '📛 To use the bot, subscribe to the channels and groups below, then tap "Check subscription".',
    "uk": "📛 Щоб користуватися ботом, підпишись на канали й групи нижче — потім натисни «Перевірити підписку».",
}
MISSING = {
    "ru": "Не хватает: {titles}",
    "en": "Still missing: {titles}",
    "uk": "Бракує: {titles}",
}
ERROR_TEXT = {
    "ru": "⚠️ Telegram не ответил, проверить подписку сейчас не вышло. Попробуй ещё раз через минуту.",
    "en": "⚠️ Telegram did not respond, the subscription check failed. Try again in a minute.",
    "uk": "⚠️ Telegram не відповів, перевірити підписку зараз не вдалося. Спробуй ще раз за хвилину.",
}
CHECK_BUTTON = {
    "ru": "✅ Проверить подписку",
    "en": "✅ Check subscription",
    "uk": "✅ Перевірити підписку",
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

OK_STATUSES = {"creator", "administrator", "member"}

CHAT_REF = re.compile(r"^[A-Za-z0-9_]{4,64}$")
LIMIT = re.compile(r"^(\d+)([smhd])$")
LIMIT_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def tr(table, lang):
    return table.get(lang) or table["ru"]


def fmt_when(moment) -> str:
    if moment is None:
        return "—"
    return moment.astimezone(KYIV).strftime("%d.%m.%Y %H:%M")


def op_alert(lang: str) -> str:
    return tr(ALERT, lang)


@dataclass
class OpGate:
    """Результат проверки для одного пользователя.

    blocked=True значит: требование активно, и пользователь ещё не прошёл
    его. Ошибки проверки тоже закрывают доступ -- «не смогли проверить» и
    «проверил, не подписан» для гейта одинаково неотличимы от «не подтверждено».
    """

    required: bool = False
    subscribed: bool = False
    missing: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    text: str | None = None
    bot_id: int | None = None

    @property
    def blocked(self) -> bool:
        return self.required and not self.subscribed


def is_active(settings: OpSettings | None, now: datetime | None = None) -> bool:
    """Тумблер требования. Действует ли ОП прямо сейчас, решает пара
    enabled + живой ресурс (op_gate, op_required_now): сроки живут на
    ресурсах, поэтому просрочка не блокирует даже до планировщика."""
    return settings is not None and bool(settings.enabled)


async def settings_for(s, bot_id):
    if bot_id is None:
        return None
    return await s.scalar(select(OpSettings).where(OpSettings.bot_id == bot_id))


async def settings_or_create(s, bot_id) -> OpSettings:
    settings = await settings_for(s, bot_id)
    if settings is None:
        settings = OpSettings(bot_id=bot_id)
        s.add(settings)
        # flush: дефолты колонок (campaign_id и счётчики) применяются на
        # INSERT -- без него первый же тумблер читал бы None.
        await s.flush()
    return settings


async def op_required_now(s, bot_id) -> bool:
    """Для очереди уведомлений: требование ещё действует?"""
    if not is_active(await settings_for(s, bot_id)):
        return False
    return bool(await _active_chats(s, bot_id))


def parse_limit(raw: str) -> timedelta:
    """'30s' / '15m' / '2h' / '1d' -> timedelta. ValueError на всё прочее."""
    match = LIMIT.fullmatch((raw or "").strip().lower())
    if match is None or int(match[1]) <= 0:
        raise ValueError("формат")
    return timedelta(**{LIMIT_UNITS[match[2]]: int(match[1])})


def _reason(error: Exception) -> str:
    if isinstance(error, TelegramForbiddenError):
        return "forbidden"
    if isinstance(error, TelegramBadRequest):
        return "bad_request"
    if isinstance(error, TelegramRetryAfter):
        return "retry_after"
    return type(error).__name__[:64]


def _status(member) -> str:
    status = getattr(member, "status", "")
    return getattr(status, "value", status) or ""


async def chat_membership(bot, chat_id, user_id) -> tuple[str, str]:
    """('ok'|'no'|'error', деталь).

    Статусы по ТЗ: creator/administrator/member -- подписка есть;
    restricted -- только с is_member=true; left/kicked -- нет. Любая ошибка
    Telegram или потеря прав -- 'error', и это подтверждением не считается."""
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


async def bot_admin_here(bot, chat_id) -> tuple[bool, str]:
    """Администратор ли бот в чате: только тогда видны чужие подписки."""
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
    except Exception as error:
        return False, _reason(error)
    status = _status(me)
    if status in {"creator", "administrator"}:
        return True, status
    return False, status or "member"


async def _try_invite(bot, chat_id) -> str | None:
    try:
        link = await bot.create_chat_invite_link(chat_id, name="Обязательная подписка")
    except Exception:
        return None
    return getattr(link, "invite_link", None)


def chat_link(chat) -> str | None:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}"
    return getattr(chat, "invite_link", None) or None


def _cache_key(bot_id, user_id) -> str:
    return f"op:gate:{bot_id}:{user_id}"


async def _cache_get(key):
    try:
        from services.chat_manager import get_redis

        return await (await get_redis()).get(key)
    except Exception:
        return None


async def _cache_set(key) -> None:
    try:
        from services.chat_manager import get_redis

        await (await get_redis()).set(key, "1", ex=CACHE_TTL)
    except Exception:
        logger.debug("event=op_cache_unavailable")


async def op_warn_allowed(bot_id, user_id) -> bool:
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


async def _bump_counters(bot_id, *, blocked: bool = False, error: bool = False) -> None:
    values = {"checks_total": OpSettings.checks_total + 1}
    if blocked:
        values["checks_blocked"] = OpSettings.checks_blocked + 1
    if error:
        values["checks_error"] = OpSettings.checks_error + 1
    try:
        async with async_session.begin() as s:
            await s.execute(
                update(OpSettings).where(OpSettings.bot_id == bot_id).values(**values)
            )
    except Exception:
        logger.debug("event=op_counters_failed bot_id=%s", bot_id)


async def _mark_rights(bot_id, chat_id, ok: bool, detail: str | None) -> None:
    try:
        async with async_session.begin() as s:
            await s.execute(
                update(OpChat)
                .where(OpChat.bot_id == bot_id, OpChat.chat_id == chat_id)
                .values(
                    rights_ok=ok,
                    rights_error=None if ok else (detail or "error")[:64],
                    verified_at=utcnow(),
                )
            )
    except Exception:
        logger.debug("event=op_rights_write_failed bot_id=%s", bot_id)


async def _active_chats(s, bot_id) -> list[OpChat]:
    """Живые ресурсы: включённые и не просроченные. Просрочка отсекается
    прямо здесь, поэтому гейт не ждёт планировщика."""
    now = utcnow()
    return list(
        (
            await s.scalars(
                select(OpChat)
                .where(
                    OpChat.bot_id == bot_id,
                    OpChat.active.is_(True),
                    or_(OpChat.ends_at.is_(None), OpChat.ends_at > now),
                )
                .order_by(OpChat.id)
            )
        ).all()
    )


async def op_gate(bot, user_id, *, force: bool = False) -> OpGate:
    """Гейт для одного пользователя.

    Читается конфигурация этого бота; если требование неактивно или таблица
    недоступна -- гейт открыт (недоступная БД уже сама по себе видна
    вотчдогами, а кирпичом из-за неё бот становиться не должен)."""
    bot_id = getattr(bot, "id", None)
    try:
        async with async_session() as s:
            settings = await settings_for(s, bot_id)
            if not is_active(settings):
                return OpGate(bot_id=bot_id)
            chats = await _active_chats(s, bot_id)
            custom = settings.text
    except Exception:
        logger.warning("event=op_settings_unavailable bot_id=%s", bot_id, exc_info=True)
        return OpGate(bot_id=bot_id)
    if not chats:
        # Ресурсы без срока кончились: требование не действует и никого
        # не запирает -- ни пользователя, ни очередь уведомлений.
        return OpGate(bot_id=bot_id)
    if not force and await _cache_get(_cache_key(bot_id, user_id)):
        return OpGate(required=True, subscribed=True, bot_id=bot_id)
    gate = OpGate(required=True, bot_id=bot_id, text=custom)
    for chat in chats:
        state, detail = await chat_membership(bot, chat.chat_id, user_id)
        if state == "ok":
            continue
        if state == "no":
            gate.missing.append(
                {"chat_id": chat.chat_id, "title": chat.title, "link": chat.link}
            )
        else:
            gate.errors.append(chat.title or str(chat.chat_id))
            if detail in {"forbidden", "bad_request"}:
                await _mark_rights(bot_id, chat.chat_id, False, detail)
    gate.subscribed = not gate.missing and not gate.errors
    if gate.subscribed:
        await _cache_set(_cache_key(bot_id, user_id))
    await _bump_counters(bot_id, blocked=gate.blocked, error=bool(gate.errors))
    return gate


def op_screen_text(gate: OpGate, lang: str) -> str:
    text = gate.text or tr(SCREEN, lang)
    if gate.missing:
        titles = ", ".join(c["title"] or str(c["chat_id"]) for c in gate.missing)
        text += "\n\n" + tr(MISSING, lang).format(titles=titles)
    if gate.errors:
        text += "\n\n" + tr(ERROR_TEXT, lang)
    return text


def op_screen_kb(gate: OpGate, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for chat in gate.missing:
        if chat.get("link"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(chat["title"] or str(chat["chat_id"]))[:60],
                        url=chat["link"],
                    )
                ]
            )
    rows.append(
        [InlineKeyboardButton(text=tr(CHECK_BUTTON, lang), callback_data="op:check")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def op_notice_text(custom: str | None, missing_titles: list[str], lang: str) -> str:
    text = custom or tr(SCREEN, lang)
    if missing_titles:
        text += "\n\n" + tr(MISSING, lang).format(titles=", ".join(missing_titles))
    return text


async def op_notice_kb(bot_id, lang: str) -> InlineKeyboardMarkup:
    async with async_session() as s:
        chats = await _active_chats(s, bot_id)
    rows = [
        [InlineKeyboardButton(text=(c.title or str(c.chat_id))[:60], url=c.link)]
        for c in chats
        if c.link
    ]
    rows.append(
        [InlineKeyboardButton(text=tr(CHECK_BUTTON, lang), callback_data="op:check")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


DIGITS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣")


def live_chats(chats, now: datetime | None = None) -> list[OpChat]:
    """Живые ресурсы среди строк: включён и не просрочен."""
    now = now or utcnow()
    return [c for c in chats if c.active and (c.ends_at is None or c.ends_at > now)]


def owner_list_text(settings, chats) -> str:
    """Меню «Активные ОП»: по ресурсу в строку, как в команде /setup."""
    live = live_chats(chats)
    if not live:
        return (
            "📭 Активных ОП нет.\n\n"
            "Запуск: /setup <@username или ID> [лимит]\n"
            "Формат времени: 30s · 15m · 2h · 1d"
        )
    lines = [f"✅ Ваши активные ОП: {len(live)}", ""]
    for index, chat in enumerate(live):
        mark = DIGITS[index] if index < len(DIGITS) else f"{index + 1}."
        lines.append(f"{mark} {chat.title or chat.chat_id}")
        lines.append(f"┣ 🆔 {chat.chat_id}")
        lines.append(f"┣ 🅰️ {chat.link or '—'}")
        if chat.ends_at is None:
            lines.append("┗ 🕒 Без ограничений")
        else:
            lines.append(f"┗ 🕒 Активна до: {fmt_when(chat.ends_at)} (Киев)")
        lines.append("")
    if settings is not None and settings.notify_running:
        lines.append(
            f"📣 Рассылка: {settings.notify_done}/{settings.notify_total}, "
            f"отправлено {settings.notify_sent}"
        )
    return "\n".join(lines).strip()


def owner_list_kb(settings, chats) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"⛔ {chat.title or chat.chat_id}"[:60],
                callback_data=f"op_owner:off:{chat.chat_id}",
            )
        ]
        for chat in live_chats(chats)
    ]
    if rows:
        if settings is not None and settings.notify_running:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⏹ Остановить рассылку",
                        callback_data="op_owner:notify_stop",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📣 Напомнить неподписанным",
                        callback_data="op_owner:notify",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Отключить всё", callback_data="op_owner:off_all"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="op_owner:list")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def op_state(bot_id):
    """(settings, все ресурсы) для меню «Активные ОП»."""
    async with async_session() as s:
        settings = await settings_for(s, bot_id)
        chats = list(
            (
                await s.scalars(
                    select(OpChat).where(OpChat.bot_id == bot_id).order_by(OpChat.id)
                )
            ).all()
        )
    return settings, chats


async def chat_row(bot_id, chat_id) -> OpChat | None:
    async with async_session() as s:
        return await s.scalar(
            select(OpChat).where(OpChat.bot_id == bot_id, OpChat.chat_id == chat_id)
        )


def parse_chat_ref(ref: str):
    """'@name' / 't.me/name' / '-100...' -> цель для get_chat, иначе None.

    Пригласительные ссылки (+hash, joinchat) не резолвятся Bot API: их
    владелец должен заменить на @username или ID."""
    text = (ref or "").strip().replace("https://", "").replace("http://", "")
    text = text.split("?", 1)[0]
    if text.startswith("t.me/"):
        text = text[5:].strip("/")
    if not text or text.startswith("+"):
        return None
    if text.startswith("@"):
        return text
    if text.startswith("joinchat/"):
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    if CHAT_REF.fullmatch(text):
        return "@" + text
    return None


async def setup_chat(
    bot, bot_id, target, admin_id: int, *, link: str | None = None, limit=None
) -> tuple[bool, str]:
    """Запускает ОП на чате: проверяет права, активирует ресурс, включает
    требование (новое включение открывает кампанию для рассылки).

    Без прав администратора ресурс не активируется: Telegram не отдаёт
    чужие подписки, и такой ресурс только запирал бы вход ошибками."""
    from services.admin import audit

    try:
        chat = await bot.get_chat(target)
    except Exception as error:
        return (
            False,
            f"Telegram не отдал этот чат ({_reason(error)}). Проверь, что бот там есть и имя верное.",
        )
    ok, detail = await bot_admin_here(bot, chat.id)
    kind = getattr(chat.type, "value", chat.type) or ""
    title = (
        getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id)
    )[:255]
    if not ok:
        return (
            False,
            f"«{title}»: бот не администратор ({detail}). Выдай права и повтори /setup.",
        )
    stored_link = link or chat_link(chat) or await _try_invite(bot, chat.id)
    now = utcnow()
    async with async_session.begin() as s:
        settings = await settings_or_create(s, bot_id)
        if not settings.enabled:
            settings.enabled = True
            settings.campaign_id += 1
            settings.starts_at = now
            audit(s, admin_id, "op_enable", "op", bot_id, after={"chat_id": chat.id})
        settings.updated_at = now
        row = await s.scalar(
            select(OpChat).where(OpChat.bot_id == bot_id, OpChat.chat_id == chat.id)
        )
        if row is None:
            row = OpChat(bot_id=bot_id, chat_id=chat.id)
            s.add(row)
        row.title, row.kind, row.link = title, kind, stored_link
        row.active = True
        row.rights_ok = True
        row.rights_error = None
        row.ends_at = now + limit if limit else None
        row.verified_at = now
        ends_at = row.ends_at
        audit(
            s,
            admin_id,
            "op_chat_add",
            "op",
            bot_id,
            after={
                "chat_id": chat.id,
                "title": title,
                "limit": str(limit) if limit else None,
            },
        )
    when = f"до {fmt_when(ends_at)} (Киев)" if ends_at else "без ограничений"
    return True, f"✅ ОП запущена: «{title}» — {when}."


async def unsetup_chat(bot_id, chat_id, admin_id: int) -> tuple[bool, str]:
    """Снимает ресурс с требования. Когда живых ресурсов не остаётся,
    требование гасится целиком -- гейту больше нечего проверять."""
    from services.admin import audit

    async with async_session.begin() as s:
        row = await s.scalar(
            select(OpChat).where(OpChat.bot_id == bot_id, OpChat.chat_id == chat_id)
        )
        if row is None or not row.active:
            return False, "Этот ресурс уже отключён."
        title = row.title or str(chat_id)
        row.active = False
        row.ends_at = None
        audit(s, admin_id, "op_chat_off", "op_chat", chat_id, after={"title": title})
        settings = await settings_for(s, bot_id)
        if settings is not None and settings.enabled:
            if not await _active_chats(s, bot_id):
                settings.enabled = False
                audit(
                    s,
                    admin_id,
                    "op_disable",
                    "op",
                    bot_id,
                    after={"reason": "no_live_chats"},
                )
    return True, f"⛔ ОП отключена: «{title}»."


async def unsetup_target(bot, bot_id, target, admin_id: int) -> tuple[bool, str]:
    """Снятие по цели из команды: @username/ссылка резолвятся в chat_id."""
    chat_id = target if isinstance(target, int) else None
    if chat_id is None:
        try:
            chat = await bot.get_chat(target)
            chat_id = chat.id
        except Exception as error:
            return False, f"Telegram не отдал этот чат ({_reason(error)})."
    return await unsetup_chat(bot_id, chat_id, admin_id)


async def unsetup_all(bot_id, admin_id: int) -> int:
    """Гасит все ресурсы и требование. Возвращает число снятых ресурсов."""
    from services.admin import audit

    async with async_session.begin() as s:
        settings = await settings_or_create(s, bot_id)
        rows = list(
            (
                await s.scalars(
                    select(OpChat).where(
                        OpChat.bot_id == bot_id, OpChat.active.is_(True)
                    )
                )
            ).all()
        )
        for row in rows:
            row.active = False
            row.ends_at = None
        settings.enabled = False
        settings.updated_at = utcnow()
        audit(
            s,
            admin_id,
            "op_disable",
            "op",
            bot_id,
            after={"reason": "unsetup_all", "chats": len(rows)},
        )
    return len(rows)


async def requirement_active(bot_id) -> bool:
    """Действует ли требование сейчас -- одной строкой для кнопок меню."""
    async with async_session() as s:
        return await op_required_now(s, bot_id)


async def tick() -> int:
    """Планировщик: гасит просроченные ресурсы и требования без живых.

    Для гейта это не обязательно -- _active_chats() уже отсекает просрочку
    на каждом запросе, -- но состояние в базе обязано показывать правду,
    не дожидаясь действий владельца."""
    from services.admin import audit

    changed = 0
    try:
        async with async_session.begin() as s:
            now = utcnow()
            expired = list(
                (
                    await s.scalars(
                        select(OpChat).where(
                            OpChat.active.is_(True),
                            OpChat.ends_at.is_not(None),
                            OpChat.ends_at <= now,
                        )
                    )
                ).all()
            )
            for chat in expired:
                chat.active = False
                changed += 1
                audit(
                    s,
                    0,
                    "op_chat_expired",
                    "op_chat",
                    chat.chat_id,
                    before={"ends_at": chat.ends_at.isoformat()},
                    after={"active": False},
                )
                logger.info(
                    "event=op_chat_expired bot_id=%s chat_id=%s",
                    chat.bot_id,
                    chat.chat_id,
                )
            for bot_id in {chat.bot_id for chat in expired}:
                settings = await settings_for(s, bot_id)
                if settings is None or not settings.enabled:
                    continue
                if await _active_chats(s, bot_id):
                    continue
                settings.enabled = False
                changed += 1
                audit(
                    s,
                    0,
                    "op_auto_disable",
                    "op",
                    bot_id,
                    before={"enabled": True},
                    after={"enabled": False, "reason": "no_live_chats"},
                )
                logger.info("event=op_auto_disabled bot_id=%s", bot_id)
    except Exception:
        logger.warning("event=op_tick_failed", exc_info=True)
    return changed


async def request_notify(bot_id, admin_id: int) -> int:
    """Ставит рассылку требования: воркер обрабатывает её порциями."""
    from services.admin import audit

    async with async_session.begin() as s:
        settings = await settings_or_create(s, bot_id)
        total = await s.scalar(
            select(func.count())
            .select_from(User)
            .where(User.bot_id == bot_id, User.deleted_at.is_(None))
        )
        settings.notify_running = True
        settings.notify_cursor = 0
        settings.notify_done = 0
        settings.notify_sent = 0
        settings.notify_total = total or 0
        audit(s, admin_id, "op_notify_start", "op", bot_id, after={"total": total or 0})
    return total or 0


async def stop_notify(bot_id, admin_id: int) -> None:
    from services.admin import audit

    async with async_session.begin() as s:
        settings = await settings_or_create(s, bot_id)
        settings.notify_running = False
        audit(s, admin_id, "op_notify_stop", "op", bot_id)


async def _user_missing(bot, chats, user_id) -> tuple[list[str], bool, bool, float]:
    missing: list[str] = []
    had_error = False
    wait = 0.0
    for chat in chats:
        state, detail = await chat_membership(bot, chat.chat_id, user_id)
        if state == "ok":
            continue
        if state == "error":
            had_error = True
            if detail.startswith("retry_after:"):
                try:
                    wait = max(wait, float(detail.split(":", 1)[1]))
                except ValueError:
                    pass
            continue
        missing.append(chat.title or str(chat.chat_id))
    return missing, (not missing and not had_error), had_error, wait


async def notify_tick() -> bool:
    """Одна порция рассылки на каждую запущенную кампанию. True -- что-то делали."""
    try:
        async with async_session() as s:
            running = list(
                (
                    await s.scalars(
                        select(OpSettings).where(OpSettings.notify_running.is_(True))
                    )
                ).all()
            )
    except Exception:
        return False
    for settings in running:
        try:
            await _notify_batch(settings.bot_id)
        except Exception:
            logger.warning(
                "event=op_notify_batch_failed bot_id=%s", settings.bot_id, exc_info=True
            )
    return bool(running)


async def _notify_batch(bot_id) -> None:
    from models.operations import Notification
    from services.notifications import enqueue
    from services.onboarding import banned
    from utils import bots

    bot = bots.resolve(bot_id)
    async with async_session() as s:
        settings = await settings_for(s, bot_id)
        if settings is None or not settings.notify_running:
            return
        chats = await _active_chats(s, bot_id)
        if not is_active(settings) or bot is None or not chats:
            settings.notify_running = False
            await s.commit()
            return
        cursor = settings.notify_cursor
        campaign = settings.campaign_id
        custom = settings.text
        users = list(
            (
                await s.scalars(
                    select(User)
                    .where(
                        User.bot_id == bot_id,
                        User.deleted_at.is_(None),
                        User.telegram_id > cursor,
                    )
                    .order_by(User.telegram_id)
                    .limit(NOTIFY_BATCH)
                )
            ).all()
        )
        if not users:
            settings.notify_running = False
            from services.admin import audit

            audit(
                s,
                0,
                "op_notify_done",
                "op",
                bot_id,
                after={"done": settings.notify_done, "sent": settings.notify_sent},
            )
            await s.commit()
            return
    done = sent = 0
    last_cursor = cursor
    for user in users:
        if banned(user) or user.bot_blocked_at:
            done += 1
            last_cursor = user.telegram_id
            continue
        missing, ok_all, had_error, wait = await _user_missing(
            bot, chats, user.telegram_id
        )
        if missing and not ok_all and not had_error:
            key = f"op_notice:{bot_id}:{user.telegram_id}:{campaign}"
            text = op_notice_text(
                custom, missing, getattr(user, "language", None) or "ru"
            )
            async with async_session.begin() as s2:
                exists = await s2.scalar(
                    select(Notification.id).where(Notification.dedupe_key == key)
                )
                if not exists:
                    await enqueue(
                        s2, user.telegram_id, "op_notice", key, text, bot_id=bot_id
                    )
                    sent += 1
        if wait:
            await _sleep(min(wait, 60))
            break
        done += 1
        last_cursor = user.telegram_id
        await _sleep(NOTIFY_PAUSE)
    async with async_session.begin() as s3:
        fresh = await settings_for(s3, bot_id)
        if fresh is None:
            return
        fresh.notify_cursor = max(fresh.notify_cursor, last_cursor)
        fresh.notify_done += done
        fresh.notify_sent += sent


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
