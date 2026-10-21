"""Friendships and the ten-minute "return to the previous partner" window.

Callbacks never carry a peer's Telegram id: friend actions use the internal
friendship row id, and both post-chat actions use the ended chat session id.
Every state change is transactional and takes the same matching advisory lock
as search, so a friend invite cannot race a random match into two chats.
"""

import logging
from datetime import timedelta

from sqlalchemy import and_, delete, func, or_, select, update

import config
from models.base import async_session
from models.chat import ActiveParticipant, Block, Chat
from models.community import Friendship, ReconnectOffer
from models.user import User
from repositories.chats import ChatRepository, lock_matching
from services.chats import ChatService
from services.result import ServiceResult
from keyboards import texts as T
from utils.time import utcnow

logger = logging.getLogger(__name__)


def _pair(a, b):
    return (a, b) if a < b else (b, a)


def _other(row, user_id):
    return row.user2_id if row.user1_id == user_id else row.user1_id


async def _lang(s, user_id):
    return (
        await s.scalar(select(User.language).where(User.telegram_id == user_id)) or "ru"
    )


async def _anon(s, user_id):
    return (
        await s.scalar(select(User.anon_id).where(User.telegram_id == user_id))
        or "Аноним"
    )


async def _blocked(s, a, b):
    return await s.scalar(
        select(Block).where(
            or_(
                and_(Block.blocker_id == a, Block.blocked_id == b),
                and_(Block.blocker_id == b, Block.blocked_id == a),
            )
        )
    )


async def _active(s, user_id):
    return await s.scalar(
        select(ActiveParticipant).where(ActiveParticipant.user_id == user_id)
    )


async def _accepted_count(s, user_id):
    return int(await s.scalar(select(func.count(Friendship.id)).where(
        Friendship.status == "accepted",
        or_(Friendship.user1_id == user_id, Friendship.user2_id == user_id),
    )) or 0)


async def _notify_accepted(s, row):
    requester = row.requested_by
    addressee = _other(row, requester)
    anon = await _anon(s, addressee)
    lang = await _lang(s, requester)
    from services.notifications import enqueue

    await enqueue(
        s,
        requester,
        "friend_accepted",
        f"friend_ok:{row.id}",
        T.tr(T.FRIEND_NOTIFY, lang)["accepted"].format(anon=anon),
        "profile:friends",
    )


class FriendService:
    async def request(self, user_id, session_id):
        """Ask the partner from an ended dialogue to become a friend."""
        async with async_session.begin() as s:
            await lock_matching(s)
            chat = await s.scalar(
                select(Chat).where(
                    Chat.session_id == session_id,
                    Chat.status == "ended",
                    or_(Chat.user1_id == user_id, Chat.user2_id == user_id),
                )
            )
            if not chat:
                return ServiceResult(False, "NOT_FOUND")
            partner = _other(chat, user_id)
            if await _blocked(s, user_id, partner):
                return ServiceResult(False, "BLOCKED")
            a, b = _pair(user_id, partner)
            existing = await s.scalar(
                select(Friendship)
                .where(Friendship.user1_id == a, Friendship.user2_id == b)
                .with_for_update()
            )
            now = utcnow()
            if existing:
                if existing.status == "accepted":
                    return ServiceResult(False, "ALREADY_FRIENDS")
                if existing.status == "blocked":
                    return ServiceResult(False, "BLOCKED")
                if existing.status == "pending":
                    ttl = timedelta(
                        days=max(1, config.FRIEND_REQUEST_TTL_DAYS)
                    )
                    if existing.updated_at < now - ttl:
                        # A stale request must not block the pair forever. Let
                        # the normal declined->pending path below replace it.
                        existing.status = "declined"
                        existing.updated_at = now - timedelta(days=2)
                    elif existing.requested_by == partner:
                        if await _accepted_count(s, user_id) >= config.MAX_FRIENDS:
                            return ServiceResult(False, "LIMIT")
                        existing.status, existing.updated_at = "accepted", now
                        await _notify_accepted(s, existing)
                        return ServiceResult(
                            True, "ACCEPTED", {"friendship_id": existing.id}
                        )
                    else:
                        return ServiceResult(False, "ALREADY_PENDING")
                if (
                    existing.status == "declined"
                    and existing.updated_at > now - timedelta(days=1)
                ):
                    return ServiceResult(False, "COOLDOWN")
                existing.requested_by = user_id
                existing.status, existing.session_id, existing.updated_at = (
                    "pending",
                    session_id,
                    now,
                )
                friendship = existing
            else:
                if await _accepted_count(s, user_id) >= config.MAX_FRIENDS:
                    return ServiceResult(False, "LIMIT")
                pending = await s.scalar(
                    select(func.count(Friendship.id)).where(
                        Friendship.requested_by == user_id,
                        Friendship.status == "pending",
                    )
                )
                if pending >= config.MAX_FRIEND_REQUESTS:
                    return ServiceResult(False, "LIMIT")
                friendship = Friendship(
                    user1_id=a,
                    user2_id=b,
                    requested_by=user_id,
                    status="pending",
                    session_id=session_id,
                    created_at=now,
                    updated_at=now,
                )
                s.add(friendship)
            await s.flush()
            anon = await _anon(s, user_id)
            lang = await _lang(s, partner)
            from services.notifications import enqueue

            await enqueue(
                s,
                partner,
                "friend_request",
                f"friend_req:{friendship.id}:{int(now.timestamp())}",
                T.tr(T.FRIEND_NOTIFY, lang)["request"].format(anon=anon),
                str(friendship.id),
            )
            return ServiceResult(True, "SENT", {"friendship_id": friendship.id})

    async def respond(self, user_id, friendship_id, accept: bool):
        if not 0 < friendship_id <= 2**63 - 1:
            return ServiceResult(False, "NOT_FOUND")
        async with async_session.begin() as s:
            row = await s.get(Friendship, friendship_id, with_for_update=True)
            if not row or user_id not in (row.user1_id, row.user2_id):
                return ServiceResult(False, "NOT_FOUND")
            if row.status != "pending":
                return ServiceResult(False, "ALREADY")
            if row.requested_by == user_id:
                return ServiceResult(False, "NOT_ADDRESSEE")
            now = utcnow()
            if row.updated_at < now - timedelta(
                days=max(1, config.FRIEND_REQUEST_TTL_DAYS)
            ):
                row.status, row.updated_at = "declined", now
                return ServiceResult(False, "EXPIRED")
            if accept:
                if await _accepted_count(s, user_id) >= config.MAX_FRIENDS:
                    return ServiceResult(False, "LIMIT")
                row.status, row.updated_at = "accepted", now
                await _notify_accepted(s, row)
                return ServiceResult(True, "ACCEPTED", {"friendship_id": row.id})
            row.status, row.updated_at = "declined", now
            return ServiceResult(True, "DECLINED", {"friendship_id": row.id})

    async def list(self, user_id):
        async with async_session() as s:
            rows = list(
                (
                    await s.scalars(
                        select(Friendship)
                        .where(
                            Friendship.status == "accepted",
                            or_(
                                Friendship.user1_id == user_id,
                                Friendship.user2_id == user_id,
                            ),
                        )
                        .order_by(Friendship.updated_at.desc())
                        .limit(config.MAX_FRIENDS)
                    )
                ).all()
            )
            ids = [_other(row, user_id) for row in rows]
            names = (
                {
                    u.telegram_id: u.anon_id
                    for u in (
                        await s.scalars(select(User).where(User.telegram_id.in_(ids)))
                    ).all()
                }
                if ids
                else {}
            )
            return [
                (row.id, names.get(_other(row, user_id), "Удалённый профиль"))
                for row in rows
            ]

    async def invite(self, user_id, friendship_id):
        """Send one friend a chat invitation; the other side accepts it."""
        if not 0 < friendship_id <= 2**63 - 1:
            return ServiceResult(False, "NOT_FOUND")
        async with async_session.begin() as s:
            row = await s.get(Friendship, friendship_id, with_for_update=True)
            if not row or user_id not in (row.user1_id, row.user2_id):
                return ServiceResult(False, "NOT_FOUND")
            if row.status != "accepted":
                return ServiceResult(False, "NOT_FRIEND")
            other = _other(row, user_id)
            if await _blocked(s, user_id, other):
                return ServiceResult(False, "BLOCKED")
            if await _active(s, other):
                return ServiceResult(False, "BUSY")
            cooldown = False
            try:
                from services.chat_manager import get_redis

                cooldown = not await (await get_redis()).set(
                    f"friend:invite:{row.id}:{user_id}", "1", ex=600, nx=True
                )
            except Exception:
                logger.debug("event=friend_invite_cooldown_unavailable", exc_info=True)
            if cooldown:
                return ServiceResult(False, "COOLDOWN")
            anon = await _anon(s, user_id)
            lang = await _lang(s, other)
            from services.notifications import enqueue

            bucket = int(utcnow().timestamp() // 600)
            await enqueue(
                s,
                other,
                "friend_invite",
                f"friend_invite:{row.id}:{bucket}",
                T.tr(T.FRIEND_NOTIFY, lang)["invite"].format(anon=anon),
                str(row.id),
            )
            return ServiceResult(True, "INVITED", {"friendship_id": row.id})

    async def join(self, user_id, friendship_id):
        if not 0 < friendship_id <= 2**63 - 1:
            return ServiceResult(False, "NOT_FOUND")
        async with async_session() as s:
            row = await s.get(Friendship, friendship_id)
            if not row or user_id not in (row.user1_id, row.user2_id):
                return ServiceResult(False, "NOT_FOUND")
            if row.status != "accepted":
                return ServiceResult(False, "NOT_FRIEND")
            other = _other(row, user_id)
        from services.matching import MatchingService

        await MatchingService().cancel(user_id)
        await MatchingService().cancel(other)
        chat = await ChatRepository().start_direct(user_id, other)
        if not chat:
            return ServiceResult(False, "BUSY")
        try:
            await ChatService().sync(chat)
        except Exception:
            logger.warning(
                "event=direct_chat_sync_failed session_id=%s", chat.session_id
            )
        return ServiceResult(True, "JOINED", {"chat": chat})


class ReconnectService:
    async def press(self, user_id, session_id):
        """Register one side's wish to return; create the chat when both agree."""
        async with async_session.begin() as s:
            await lock_matching(s)
            offer = await s.scalar(
                select(ReconnectOffer)
                .where(ReconnectOffer.session_id == session_id)
                .with_for_update()
            )
            if not offer or offer.status != "pending":
                return ServiceResult(False, "ALREADY_MATCHED")
            now = utcnow()
            if offer.expires_at <= now:
                offer.status, offer.updated_at = "expired", now
                return ServiceResult(False, "EXPIRED")
            if user_id not in (offer.user1_id, offer.user2_id):
                return ServiceResult(False, "NOT_FOUND")
            if await _active(s, user_id):
                return ServiceResult(False, "BUSY")
            if await _blocked(s, offer.user1_id, offer.user2_id):
                offer.status, offer.updated_at = "blocked", now
                return ServiceResult(False, "BLOCKED")
            if user_id == offer.user1_id:
                if offer.user1_wants:
                    return ServiceResult(True, "WAITING")
                offer.user1_wants = True
            else:
                if offer.user2_wants:
                    return ServiceResult(True, "WAITING")
                offer.user2_wants = True
            offer.updated_at = now
            both = offer.user1_wants and offer.user2_wants
            other = _other(offer, user_id)
            if not both:
                anon = await _anon(s, user_id)
                lang = await _lang(s, other)
                from services.notifications import enqueue

                await enqueue(
                    s,
                    other,
                    "reconnect_waiting",
                    f"reconnect_wait:{offer.session_id}:{other}",
                    T.tr(T.FRIEND_NOTIFY, lang)["reconnect"].format(anon=anon),
                    offer.session_id,
                )
                return ServiceResult(True, "WAITING")
            a, b = offer.user1_id, offer.user2_id

        from services.matching import MatchingService

        await MatchingService().cancel(a)
        await MatchingService().cancel(b)
        chat = await ChatRepository().start_direct(a, b)
        if not chat:
            async with async_session.begin() as s:
                row = await s.scalar(
                    select(ReconnectOffer)
                    .where(ReconnectOffer.session_id == session_id)
                    .with_for_update()
                )
                if row:
                    row.status, row.updated_at = "expired", utcnow()
            return ServiceResult(False, "BUSY")
        try:
            await ChatService().sync(chat)
        except Exception:
            logger.warning("event=reconnect_sync_failed session_id=%s", chat.session_id)
        async with async_session.begin() as s:
            row = await s.scalar(
                select(ReconnectOffer)
                .where(ReconnectOffer.session_id == session_id)
                .with_for_update()
            )
            if row:
                row.status, row.updated_at = "matched", utcnow()
        return ServiceResult(True, "MATCHED", {"chat": chat})


async def block_pair(s, a, b):
    """A report blocks the pair and ends any friendship between them."""
    x, y = _pair(a, b)
    await s.execute(
        update(Friendship)
        .where(
            Friendship.user1_id == x,
            Friendship.user2_id == y,
            Friendship.status.in_(["pending", "accepted"]),
        )
        .values(status="blocked", updated_at=utcnow())
    )


async def cleanup():
    now = utcnow()
    async with async_session.begin() as s:
        await s.execute(
            update(ReconnectOffer)
            .where(
                ReconnectOffer.status == "pending",
                ReconnectOffer.expires_at < now,
            )
            .values(status="expired", updated_at=now)
        )
        await s.execute(
            delete(Friendship).where(
                Friendship.status.in_(["declined", "blocked"]),
                Friendship.updated_at < now - timedelta(days=90),
            )
        )
