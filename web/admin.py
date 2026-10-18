"""Локальная админка поверх той же AdminService, что крутит Telegram-панель.

Отдельного слоя прав здесь нет и быть не должно: бан, премиум, рассылка и
смена настройки идут ровно теми же методами, поэтому в admin_audit_log
действие выглядит одинаково независимо от того, пришло оно из бота или из
браузера.

Доступ держится на токене, а не на адресе: контейнер web слушает только
127.0.0.1, но nginx однажды может открыть его наружу, и тогда база людей
окажется в интернете. Пустой ADMIN_WEB_TOKEN выключает админку целиком --
как и STATS_ACCESS_KEY выключает /api/stats.
"""
import hashlib
import hmac
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

import config
from models.base import async_session
from models.chat import Chat
from models.operations import AdminAudit, AnalyticsEvent, Broadcast, Notification
from models.user import User
from services.admin import (AdminService, BROADCAST_SEGMENTS, SETTING_DEFAULTS,
                            SETTING_LIMITS, SETTING_TITLES, StatsService)
from utils.bots import _configured_ids
from utils.bots import label as bot_label
from utils.time import kyiv_day_bounds, utcnow

router = APIRouter(prefix="/api/admin")
# Отдельный роутер под саму страницу: её нельзя вешать на /api/admin,
# иначе она уезжает вместе с префиксом API.
page_router = APIRouter()
PAGE = Path(__file__).parent / "static" / "admin" / "index.html"

# Виды, ради которых админка смотрит в notification_log: только то, что
# требует внимания владельца. Про запуски здесь один сигнал -- карточка
# первого запуска нового человека (owner_first_start); потока «зашёл снова»
# и часовых сводок в продукте нет.
OWNER_NOTICE_TYPES = ("startup", "owner_first_payment", "owner_report", "backup_failed",
                      "funnel_alert", "owner_first_start")


def _admin_id() -> int:
    """От чьего имени работают действия и пишется аудит."""
    if config.ADMIN_WEB_ID:
        return config.ADMIN_WEB_ID
    # Владелец -- тот, кто заводит рассылки из телеграм-панели; брать его,
    # иначе confirm_broadcast упрётся в "не тот admin_id" у готового черновика.
    if config.OWNER_ID in config.ADMIN_IDS:
        return config.OWNER_ID
    if config.ADMIN_IDS:
        return min(config.ADMIN_IDS)
    raise HTTPException(status_code=503, detail="ADMIN_NOT_CONFIGURED")


def _as_int(value, default=None, lo=None, hi=None):
    """int() из произвольного JSON/query: без исключений и без конверсий
    длиннее 18 цифр (лимит CPython на int() -- 4300 цифр, дальше ValueError)."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    text = str(value).strip()
    if not text or len(text) > 18 or not text.lstrip("-").isdigit():
        return default
    number = int(text)
    if lo is not None and number < lo:
        return default
    if hi is not None and number > hi:
        return default
    return number


async def _guard(request: Request) -> None:
    """Токен только из заголовка.

    ?key= больше не принимается (L1): полный токен утекал в access-лог nginx
    ($request содержит query), историю браузера и Referer. Ссылку для входа
    держат на #key=: фрагмент не уходит на сервер, а app.js дальше шлёт
    Authorization: Bearer.
    """
    if not config.ADMIN_WEB_TOKEN:
        raise HTTPException(status_code=404)
    supplied = request.headers.get("authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:]
    # Сравнение по времени не зависит от содержимого. Байты, а не строки:
    # compare_digest на str кидает TypeError на не-ASCII, и любой мусор в
    # заголовке уходил бы в 500 вместо честного 401.
    if not hmac.compare_digest(supplied.encode("utf-8", "replace"),
                               config.ADMIN_WEB_TOKEN.encode("utf-8", "replace")):
        raise HTTPException(status_code=401, detail="BAD_TOKEN")


def _require(result):
    """ServiceResult наружу: код ошибки уходит клиенту, а не тонет в 500."""
    if getattr(result, "ok", False):
        return result
    raise HTTPException(status_code=400, detail=getattr(result, "code", "FAILED"))


def _iso(value):
    return value.isoformat() if value else None


def _start_of_day():
    """Начало суток по Киеву: панель считает «сегодня» так же, как карточка
    первого запуска и админ-обзор -- по календарю Europe/Kyiv."""
    return kyiv_day_bounds()[0]


def _user_row(user, *, detail: bool = False):
    row = {
        "telegram_id": user.telegram_id,
        "anon_id": user.anon_id,
        "username": user.username,
        "first_name": user.first_name,
        "bot_id": user.bot_id,
        "created_at": _iso(user.created_at),
        "last_online": _iso(user.last_online),
        "language": user.language,
        "gender": user.gender,
        "age": user.age,
        "chats_count": user.chats_count,
        "start_count": user.start_count,
        "is_banned": user.is_banned,
        "ban_expires": _iso(user.ban_expires),
        "is_muted": user.is_muted,
        "mute_expires": _iso(user.mute_expires),
        "premium_expires": _iso(user.premium_expires),
        "onboarding_completed": user.onboarding_completed,
        "onboarding_step": user.onboarding_step,
        "total_stars_spent": user.total_stars_spent,
        "positive_rating_pct": round(user.positive_rating_pct or 0, 1),
        "deleted": bool(user.deleted_at),
        "bot": bot_label(user.bot_id),
    }
    if detail:
        row.update({
            "referrer_id": user.referrer_id,
            "referrals_count": user.referrals_count,
            "referrals_activated": user.referrals_activated,
            "bio": user.bio,
            "total_chat_seconds": user.total_chat_seconds,
            "likes_received": user.likes_received,
            "dislikes_received": user.dislikes_received,
            "streak_days": user.streak_days,
            "rules_version": user.rules_version,
        })
    return row


async def _bots_split():
    """Наблюдаемость по ботам: сколько пришло каждым и сколько дошло до меню.

    Одна цифра «запустили 37» скрывает, какой из двух ботов растёт, а это
    единственное, ради чего они и разделены."""
    today = _start_of_day()
    week = today - timedelta(days=6)
    configured = list(_configured_ids())
    async with async_session() as s:
        new_today = dict((await s.execute(
            select(User.bot_id, func.count(User.id))
            .where(User.created_at >= today).group_by(User.bot_id))).all())
        new_week = dict((await s.execute(
            select(User.bot_id, func.count(User.id))
            .where(User.created_at >= week).group_by(User.bot_id))).all())
        menu_today = dict((await s.execute(
            select(User.bot_id, func.count(User.id)).where(
                User.created_at >= today, User.onboarding_completed.is_(True)
            ).group_by(User.bot_id))).all())
    seen = set(configured) | set(new_today) | set(new_week) | set(menu_today)
    order = {bot_id: i for i, bot_id in enumerate(configured)}
    # Ключ детерминированный, иначе NULL и незнакомый id делят одно место,
    # а set отдаёт их в произвольном порядке.
    out = []
    for bot_id in sorted(seen, key=lambda b: (order.get(b, 99), b is None, b or 0)):
        out.append({
            "bot_id": bot_id,
            "label": bot_label(bot_id),
            "new_today": new_today.get(bot_id, 0),
            "new_week": new_week.get(bot_id, 0),
            "menu_today": menu_today.get(bot_id, 0),
        })
    return out


@router.get("/overview")
async def overview(days: int = Query(1, ge=1, le=90), _: None = Depends(_guard)):
    """Обзор: цифры за период, воронка, живой статус и разбивка по ботам."""
    service, stats = AdminService(), StatsService()
    data = await stats.overview(days)
    # overview() отдаёт под ключом funnel счётчики событий аналитики, а панели
    # нужны шаги онбординга -- меняем местами, не трогая сам сервис: телеграм-
    # панель читает его так, как есть.
    data["events"] = data.pop("funnel", {})
    data["funnel"] = await stats.onboarding_funnel(days)
    data["system"] = await service.system_status(_admin_id())
    data["bots"] = await _bots_split()
    async with async_session() as s:
        data["drafts"] = await s.scalar(select(func.count(Broadcast.id)).where(
            Broadcast.status == "draft")) or 0
    return data


@router.get("/users")
async def users_list(q: str = "", bot: int | None = None, banned: int = 0,
                     premium: int = 0, only_new: int = 0,
                     page: int = Query(1, ge=1, le=10**6), size: int = Query(25, ge=1, le=200),
                     _: None = Depends(_guard)):
    """Люди: поиск по id/анону/юзернейму/имени и фильтры по боту и состоянию."""
    where = []
    value = q.strip()
    if value:
        needle = value.lstrip("#").lower()
        conditions = [func.lower(User.anon_id).like(f"%{needle}%"),
                      func.lower(User.username).like(f"%{needle}%"),
                      func.lower(User.first_name).like(f"%{needle}%")]
        # 18 цифр -- безопасная верхняя граница для INTEGER в SQLite:
        # 19-значный "999..." уже шире 2^63-1, int() на нём уводил запрос
        # в OverflowError/ValueError (M1).
        if needle.isascii() and needle.isdigit() and len(needle) <= 18:
            conditions.append(User.telegram_id == int(needle))
        where.append(or_(*conditions))
    if bot is not None:
        where.append(User.bot_id.is_(None) if bot == 0 else User.bot_id == bot)
    if banned:
        where.append(User.is_banned.is_(True))
    if premium:
        where.append(User.premium_expires > utcnow())
    if only_new:
        where.append(User.created_at >= _start_of_day())
    async with async_session() as s:
        total = await s.scalar(select(func.count(User.id)).where(*where)) or 0
        rows = (await s.scalars(select(User).where(*where)
                                .order_by(User.last_online.desc())
                                .offset((page - 1) * size).limit(size))).all()
        summary = dict(new_today=await s.scalar(select(func.count(User.id)).where(
            User.created_at >= _start_of_day())) or 0,
            banned=await s.scalar(select(func.count(User.id)).where(
            User.is_banned.is_(True))) or 0,
            premium=await s.scalar(select(func.count(User.id)).where(
            User.premium_expires > utcnow())) or 0)
    return {"total": total, "page": page, "size": size, "summary": summary,
            "items": [_user_row(u) for u in rows]}


@router.get("/users/{telegram_id}")
async def user_detail(telegram_id: int, _: None = Depends(_guard)):
    """Карточка человека: профиль и последние диалоги."""
    if not 0 < telegram_id <= 2**63 - 1:
        # Шире int64: SQLite не принимает такое значение в WHERE (M1).
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    partner = aliased(User)
    # Сторона, которой собеседник не является: в чате всегда user1 и user2.
    partner_id = case((Chat.user1_id == telegram_id, Chat.user2_id),
                      else_=Chat.user1_id)
    async with async_session() as s:
        user = await s.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            raise HTTPException(status_code=404, detail="NOT_FOUND")
        chats = (await s.execute(
            select(Chat.session_id, Chat.started_at, Chat.ended_at, Chat.status,
                   Chat.duration_seconds, Chat.end_reason, Chat.user1_rating,
                   partner.anon_id, partner.bot_id, Chat.user1_id)
            .join(partner, partner.telegram_id == partner_id)
            .where(or_(Chat.user1_id == telegram_id, Chat.user2_id == telegram_id))
            .order_by(Chat.id.desc()).limit(15))).all()
        # Ключ start_source:{telegram_id}:{source} -- единственный след того,
        # откуда человек пришёл: колонки под это в users нет.
        key = await s.scalar(select(AnalyticsEvent.dedupe_key).where(
            AnalyticsEvent.event == "start_source",
            AnalyticsEvent.user_id == telegram_id).limit(1))
    source = key.rsplit(":", 1)[-1] if key else None
    return {
        "source": source,
        "user": _user_row(user, detail=True),
        "chats": [{"session_id": c[0], "started_at": _iso(c[1]), "ended_at": _iso(c[2]),
                   "status": c[3], "duration_seconds": c[4], "end_reason": c[5],
                   "rating": c[6], "partner_anon_id": c[7], "partner_bot": bot_label(c[8]),
                   "is_user1": c[9] == telegram_id} for c in chats],
    }


@router.post("/users/{telegram_id}/ban")
async def ban_user(telegram_id: int, body: dict, _: None = Depends(_guard)):
    raw = body.get("hours")
    if raw in (None, ""):
        hours = None
    else:
        hours = _as_int(raw, None, 0, 87600)
        if hours is None:
            raise HTTPException(status_code=400, detail="INVALID_HOURS")
        hours = hours or None  # 0 -- как раньше, бессрочная блокировка
    _require(await AdminService().ban(_admin_id(), telegram_id,
                                      body.get("reason", "") or "без причины",
                                      hours))
    return {"ok": True}


@router.post("/users/{telegram_id}/unban")
async def unban_user(telegram_id: int, _: None = Depends(_guard)):
    _require(await AdminService().unban(_admin_id(), telegram_id))
    return {"ok": True}


@router.post("/users/{telegram_id}/mute")
async def mute_user(telegram_id: int, body: dict, _: None = Depends(_guard)):
    raw = body.get("hours")
    if raw in (None, ""):
        hours = None
    else:
        hours = _as_int(raw, None, 0, 87600)
        if hours is None:
            raise HTTPException(status_code=400, detail="INVALID_HOURS")
        hours = hours or None  # 0 -- бессрочный мут
    _require(await AdminService().mute(_admin_id(), telegram_id,
                                       body.get("reason", "") or "без причины",
                                       hours))
    return {"ok": True}


@router.post("/users/{telegram_id}/unmute")
async def unmute_user(telegram_id: int, _: None = Depends(_guard)):
    _require(await AdminService().unmute(_admin_id(), telegram_id))
    return {"ok": True}


@router.post("/users/{telegram_id}/premium")
async def grant_premium(telegram_id: int, body: dict, _: None = Depends(_guard)):
    days = _as_int(body.get("days", 30), None, 1, 3650)
    if days is None:
        raise HTTPException(status_code=400, detail="INVALID_DAYS")
    _require(await AdminService().grant(_admin_id(), telegram_id, days,
                                        body.get("reason", "") or "выдано из админки"))
    return {"ok": True}


@router.post("/users/{telegram_id}/message")
async def message_user(telegram_id: int, body: dict, _: None = Depends(_guard)):
    # Ключ детерминирован от получателя и текста: двойной клик или ретрай
    # браузера упирается в ALREADY_SENT и не уходит второй раз. Отправить
    # буквально то же самое ещё раз -- значит немного изменить текст.
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="EMPTY")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="INVALID")
    digest = hashlib.sha256(text.encode()).hexdigest()[:32]
    result = await AdminService().send_message_to_user(
        _admin_id(), telegram_id, text, request_key=f"web:{telegram_id}:{digest}")
    if not result.ok and result.code == "ALREADY_SENT":
        raise HTTPException(status_code=409, detail="ALREADY_SENT")
    _require(result)
    return {"ok": True}


@router.get("/notifications")
async def notifications(kind: str = "owner", bot: int | None = None,
                        status: str = "",
                        page: int = Query(1, ge=1, le=10**6), size: int = Query(30, ge=1, le=200),
                        _: None = Depends(_guard)):
    """Уведомления владельцу: старт сервиса, первые оплаты, жалобы, бэкапы,
    вставшая воронка -- всё, что требует внимания."""
    where = []
    if kind == "owner":
        where.append(Notification.type.in_(OWNER_NOTICE_TYPES))
    elif kind != "all":
        where.append(Notification.type == kind)
    if bot is not None:
        where.append(Notification.bot_id.is_(None) if bot == 0 else Notification.bot_id == bot)
    if status:
        where.append(Notification.status == status)
    async with async_session() as s:
        total = await s.scalar(select(func.count(Notification.id)).where(*where)) or 0
        rows = (await s.scalars(select(Notification).where(*where)
                                .order_by(Notification.id.desc())
                                .offset((page - 1) * size).limit(size))).all()
        counts = dict((await s.execute(
            select(Notification.type, func.count(Notification.id))
            .where(Notification.type.in_(OWNER_NOTICE_TYPES))
            .group_by(Notification.type))).all())
        by_bot = (await s.execute(
            select(Notification.bot_id, Notification.status, func.count(Notification.id))
            .where(Notification.type.in_(OWNER_NOTICE_TYPES),
                   Notification.scheduled_at >= _start_of_day())
            .group_by(Notification.bot_id, Notification.status))).all()
    return {"total": total, "page": page, "size": size, "counts": counts,
            "today_by_bot": [{"bot_id": b, "bot": bot_label(b), "status": st, "count": c}
                             for b, st, c in by_bot],
            "items": [{"id": n.id, "type": n.type, "bot_id": n.bot_id,
                       "bot": bot_label(n.bot_id), "status": n.status, "text": n.text,
                       "scheduled_at": _iso(n.scheduled_at), "sent_at": _iso(n.sent_at),
                       "last_error": n.last_error, "user_id": n.user_id}
                      for n in rows]}


@router.get("/audit")
async def audit_log(page: int = Query(1, ge=1, le=10**6), size: int = Query(25, ge=1, le=200),
                    action: str = "", _: None = Depends(_guard)):
    """Что админы делали: баны, премиум, рассылки, смена настроек."""
    where = [AdminAudit.action == action] if action else []
    async with async_session() as s:
        total = await s.scalar(select(func.count(AdminAudit.id)).where(*where)) or 0
        rows = (await s.scalars(select(AdminAudit).where(*where)
                                .order_by(AdminAudit.id.desc())
                                .offset((page - 1) * size).limit(size))).all()
        actions = (await s.execute(select(AdminAudit.action, func.count(AdminAudit.id))
                                   .group_by(AdminAudit.action))).all()
    return {"total": total, "page": page, "size": size,
            "actions": [{"action": a, "count": c} for a, c in sorted(actions)],
            "items": [{"id": r.id, "admin_id": r.admin_id, "action": r.action,
                       "target_type": r.target_type, "target_id": r.target_id,
                       "before": r.before_json, "after": r.after_json,
                       "created_at": _iso(r.created_at)} for r in rows]}


@router.get("/settings")
async def settings_all(_: None = Depends(_guard)):
    """Каталог настроек: текущее значение, границы поля и дефолт."""
    values = await AdminService().settings_all(_admin_id())
    return {"items": [{"key": key, "title": SETTING_TITLES.get(key, key),
                       "value": values.get(key),
                       "min": SETTING_LIMITS[key][0], "max": SETTING_LIMITS[key][1],
                       "default": SETTING_DEFAULTS.get(key)}
                      for key in SETTING_LIMITS]}


@router.post("/settings")
async def setting_save(body: dict, _: None = Depends(_guard)):
    # Не-число/объект/5000 цифр -> default -1, сервис отвечает INVALID (400).
    value = _as_int(body.get("value", -1), -1, 0, 10**9)
    _require(await AdminService().setting(_admin_id(), body.get("key", ""), value))
    return {"ok": True}


@router.get("/broadcasts")
async def broadcasts(_: None = Depends(_guard)):
    """Все рассылки, не только свои: видеть чужую волю важнее, чем её прятать.

    confirm/cancel зовутся от _admin_id(), поэтому чужая черновика остаётся
    черновиком -- owned=false гасит кнопку, а не роняет запрос в 400."""
    admin = _admin_id()
    async with async_session() as s:
        rows = (await s.scalars(select(Broadcast).order_by(Broadcast.id.desc())
                                .limit(30))).all()
    return {"segments": list(BROADCAST_SEGMENTS), "admin_id": admin,
            "items": [{"id": j.id, "admin_id": j.admin_id, "owned": j.admin_id == admin,
                       "segment": j.segment, "kind": j.kind, "content": j.content,
                       "poll_options": j.poll_options, "status": j.status,
                       "total": j.total, "sent": j.sent, "failed": j.failed,
                       "blocked": j.blocked, "created_at": _iso(j.created_at),
                       "started_at": _iso(j.started_at), "finished_at": _iso(j.finished_at)}
                      for j in rows]}


@router.get("/broadcasts/audience")
async def broadcast_audience(segment: str = "all", _: None = Depends(_guard)):
    """Размер сегмента без создания черновики -- кнопка «Посчитать» не должна
    оставлять за собой мусор в списке рассылок."""
    return {"segment": segment,
            "audience": await AdminService().audience_size(_admin_id(), segment)}


@router.post("/broadcasts")
async def broadcast_create(body: dict, _: None = Depends(_guard)):
    """Черновик + оценка аудитории. Отправка -- отдельным шагом confirm:
    увидеть размер до разъезда важнее, чем сэкономить тап."""
    service, admin = AdminService(), _admin_id()
    kind = body.get("kind") or "text"
    options = [line.strip() for line in (body.get("options") or "").split("\n") if line.strip()]
    result = await service.broadcast(admin, body.get("segment", "all"),
                                     body.get("content", ""), kind,
                                     options if kind == "poll" else None)
    _require(result)
    job = result.data["job"]
    return {"ok": True, "id": job.id,
            "audience": await service.audience_size(admin, job.segment)}


@router.post("/broadcasts/{job_id}/confirm")
async def broadcast_confirm(job_id: int, _: None = Depends(_guard)):
    _require(await AdminService().confirm_broadcast(_admin_id(), job_id))
    return {"ok": True}


@router.post("/broadcasts/{job_id}/cancel")
async def broadcast_cancel(job_id: int, _: None = Depends(_guard)):
    _require(await AdminService().cancel_broadcast(_admin_id(), job_id))
    return {"ok": True}


@router.get("/reports")
async def reports(offset: int = Query(0, ge=0, le=10**9), _: None = Depends(_guard)):
    """Жалобы, ожидающие решения."""
    rows = await AdminService().reports(_admin_id(), offset)
    return {"items": [{"id": r.id, "reporter_id": r.reporter_id,
                       "target_id": r.reported_id, "reason": r.reason,
                       "status": r.status, "created_at": _iso(r.created_at),
                       "message": (r.message_content or "")[:200]}
                      for r in (rows or [])]}


@router.post("/reports/{report_id}/resolve")
async def report_resolve(report_id: int, body: dict, _: None = Depends(_guard)):
    action = body.get("action", "dismiss")
    if action not in {"ban1", "banf", "mute1", "mute24", "mute7", "dismiss"}:
        raise HTTPException(status_code=400, detail="INVALID")
    _require(await AdminService().resolve(_admin_id(), report_id, action))
    return {"ok": True}


@page_router.get("/admin/")
async def admin_page():
    """Сама страница.

    Токен здесь не проверяется намеренно: открывают по ссылке, где заголовка
    ещё нет, а данные всё равно недоступны без него. Зато JS успевает
    вытащить ?key= из адреса и вычистить его оттуда."""
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})
