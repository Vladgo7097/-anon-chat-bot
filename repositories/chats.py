import uuid
import logging
from datetime import timedelta
from sqlalchemy import select, update, delete, or_, and_, text
import config
from models.base import async_session
from models.user import User
from models.chat import Chat, ActiveParticipant, Block, Rating, RevealRequest
from models.community import ReconnectOffer
from models.preview import PreviewOffer
from services.onboarding import step_for, banned, muted
from utils.time import utcnow
from services.premium import premium_active


def compatible(a, b, gender_access=()):
    if any(
        step_for(u) != "MENU"
        or banned(u)
        or muted(u)
        or u.deleted_at
        or u.bot_blocked_at
        for u in (a, b)
    ):
        return False
    for own, other in ((a, b), (b, a)):
        if (
            step_for(own) != "MENU"
            or banned(own)
            or own.deleted_at
            or own.bot_blocked_at
        ):
            return False
        if premium_active(own) or own.telegram_id in gender_access:
            gender = {"m": "male", "f": "female", "a": "any"}.get(
                own.settings_gender_filter, own.settings_gender_filter
            )
            if gender != "any" and gender != other.gender:
                return False
            if (
                premium_active(own)
                and not own.settings_age_min <= other.age <= own.settings_age_max
            ):
                return False
    return a.telegram_id != b.telegram_id


ICEBREAKERS = (
    "«Как прошёл твой день?»",
    "«Что тебя сегодня порадовало?»",
    "«Кофе или чай?»",
    "«Что последнее смотрел или читал?»",
    "«Сова или жаворонок?»",
    "«Куда бы уехал прямо сейчас?»",
    "«Какая музыка играет у тебя чаще всего?»",
    "«Море или горы?»",
    "«Чем занимаешься по вечерам?»",
    "«Что умеешь такого, чему сложно научиться?»",
)


def icebreaker(session_id):
    """Same prompt for both sides of one dialogue, stable for a given session."""
    return ICEBREAKERS[int(session_id[:8], 16) % len(ICEBREAKERS)]


MATCH_FOUND = {
    "ru": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Собеседник найден!\n\nПишите первым! <tg-emoji emoji-id="5350596259365273418">👋</tg-emoji>\n\n<tg-emoji emoji-id="5312160339335347417">💡</tg-emoji> Если не знаешь, с чего начать: ',
    "en": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Match found!\n\nSay hi first! <tg-emoji emoji-id="5350596259365273418">👋</tg-emoji>\n\n<tg-emoji emoji-id="5312160339335347417">💡</tg-emoji> Not sure how to start: ',
    "uk": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Співрозмовника знайдено!\n\nПиши першим! <tg-emoji emoji-id="5350596259365273418">👋</tg-emoji>\n\n<tg-emoji emoji-id="5312160339335347417">💡</tg-emoji> Якщо не знаєш, з чого почати: ',
}


async def _notify_match(s, chat, users):
    """Queue the same "match found" notice for both sides of a new chat."""
    from services.growth import event
    from locales import tr

    starter = icebreaker(chat.session_id)
    by_id = {u.telegram_id: u for u in users}
    for uid in (chat.user1_id, chat.user2_id):
        await event(s, uid, "chat_started", f"chat_started:{chat.session_id}:{uid}")
        from services.notifications import enqueue

        lang = getattr(by_id.get(uid), "language", None) or "ru"
        await enqueue(
            s,
            uid,
            "match",
            f"match:{chat.session_id}:{uid}",
            tr(MATCH_FOUND, lang) + starter,
            chat.session_id,
        )


async def lock_matching(session):
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(8873771496)"))


class ChatRepository:
    async def compatible_candidates(self, user_id, candidate_ids):
        """Cheap snapshot selection. match() rechecks everything under its lock."""
        from models.payment import Entitlement

        async with async_session() as s:
            ids = [user_id, *candidate_ids]
            users = {
                u.telegram_id: u
                for u in (
                    await s.scalars(select(User).where(User.telegram_id.in_(ids)))
                ).all()
            }
            own = users.get(user_id)
            if own is None:
                return []
            active = set(
                (
                    await s.scalars(
                        select(ActiveParticipant.user_id).where(
                            ActiveParticipant.user_id.in_(ids)
                        )
                    )
                ).all()
            )
            if user_id in active:
                return []
            reserved = await self.reserved_users(s, ids)
            if user_id in reserved:
                return []
            access = set(
                (
                    await s.scalars(
                        select(Entitlement.user_id).where(
                            Entitlement.user_id.in_(ids),
                            Entitlement.kind.in_(
                                ["gender_filter_one_match", "gender_filter_1m"]
                            ),
                            Entitlement.remaining_uses > 0,
                            or_(
                                Entitlement.expires_at.is_(None),
                                Entitlement.expires_at > utcnow(),
                            ),
                        )
                    )
                ).all()
            )
            blocks = (
                await s.execute(
                    select(Block.blocker_id, Block.blocked_id).where(
                        or_(Block.blocker_id == user_id, Block.blocked_id == user_id)
                    )
                )
            ).all()
            blocked = {b if a == user_id else a for a, b in blocks}
            soft = [
                uid
                for uid in candidate_ids
                if uid != user_id
                and uid in users
                and uid not in active
                and uid not in reserved
                and uid not in blocked
                and compatible(own, users[uid], access)
            ]
            # Анти-повтор: недавние собеседники уступают место свежим
            # кандидатам. Запасной выход -- если фильтр вымер все живые
            # варианты страницы, вернуть самого свежего повторного: узкий
            # пул не должен переводить людей в вечное ожидание.
            minutes = int(config.MATCH_COOLDOWN_MINUTES)
            if minutes <= 0 or not soft:
                return soft
            recent = await self.recent_partners(s, user_id, [uid for uid in soft])
            fresh = [uid for uid in soft if uid not in recent]
            if fresh:
                return fresh
            return [max(recent, key=recent.get)]

    @staticmethod
    async def recent_partners(session, user_id, candidate_ids, minutes=None):
        """Из кандидатов -- кто был парой user_id за последние N минут.

        Возвращает {telegram_id: время последнего события пары}. Один
        источник правды для анти-повтора: и выборка кандидатов, и любая
        будущая проверка читают отсюда. События пары -- не только чаты:
        отказ от preview-карточки и её истечение тоже уходят в кулдаун,
        отказ дольше (человек осознанно сказал «нет»), молчание короче,
        чтобы тихий пул не переносил людей в вечное ожидание.
        """
        if minutes is None:
            minutes = int(config.MATCH_COOLDOWN_MINUTES)
        if not candidate_ids:
            return {}
        ids = list(candidate_ids)
        recent = {}
        if minutes > 0:
            since = utcnow() - timedelta(minutes=minutes)
            rows = (
                await session.execute(
                    select(Chat.user1_id, Chat.user2_id, Chat.started_at)
                    .where(
                        or_(
                            and_(Chat.user1_id == user_id, Chat.user2_id.in_(ids)),
                            and_(Chat.user2_id == user_id, Chat.user1_id.in_(ids)),
                        ),
                        Chat.started_at > since,
                    )
                    .order_by(Chat.started_at.desc())
                    .limit(500)
                )
            ).all()
            for u1, u2, started in rows:
                other = u2 if u1 == user_id else u1
                # Строки идут от свежих к старым -- первое попадание и есть
                # последний совместный чат.
                recent.setdefault(other, started)
        declined_minutes = int(config.PREVIEW_DECLINED_COOLDOWN_MINUTES)
        expired_minutes = int(config.PREVIEW_EXPIRED_COOLDOWN_MINUTES)
        if declined_minutes <= 0 and expired_minutes <= 0:
            return recent
        cutoffs = {}
        for status, minutes_value in (
            ("declined", declined_minutes),
            ("expired", expired_minutes),
        ):
            if minutes_value > 0:
                cutoffs[status] = utcnow() - timedelta(minutes=minutes_value)
        if not cutoffs:
            return recent
        window = utcnow() - timedelta(minutes=max(declined_minutes, expired_minutes))
        offer_rows = (
            await session.execute(
                select(
                    PreviewOffer.user1_id,
                    PreviewOffer.user2_id,
                    PreviewOffer.status,
                    PreviewOffer.resolved_at,
                )
                .where(
                    or_(
                        and_(
                            PreviewOffer.user1_id == user_id,
                            PreviewOffer.user2_id.in_(ids),
                        ),
                        and_(
                            PreviewOffer.user2_id == user_id,
                            PreviewOffer.user1_id.in_(ids),
                        ),
                    ),
                    PreviewOffer.status.in_(tuple(cutoffs)),
                    PreviewOffer.resolved_at.is_not(None),
                    PreviewOffer.resolved_at > window,
                )
                .order_by(PreviewOffer.resolved_at.desc())
                .limit(500)
            )
        ).all()
        for u1, u2, status, resolved in offer_rows:
            cutoff = cutoffs.get(status)
            if cutoff is not None and resolved > cutoff:
                other = u2 if u1 == user_id else u1
                recent.setdefault(other, resolved)
        return recent

    async def active(self, user_id):
        async with async_session() as s:
            return await s.scalar(
                select(Chat)
                .join(
                    ActiveParticipant, ActiveParticipant.session_id == Chat.session_id
                )
                .where(ActiveParticipant.user_id == user_id, Chat.status == "active")
            )

    async def get(self, session_id, user_id):
        async with async_session() as s:
            return await s.scalar(
                select(Chat).where(
                    Chat.session_id == session_id,
                    or_(Chat.user1_id == user_id, Chat.user2_id == user_id),
                )
            )

    async def match(self, a_id, b_id, redis):
        async with async_session.begin() as s:
            await lock_matching(s)
            pair = await self.load_pair(s, redis, a_id, b_id)
            if pair is None:
                return None
            users, filters = pair
            chat = await self.finalize_chat(s, a_id, b_id, users, filters)
        logging.getLogger(__name__).info(
            "chat_matched", extra={"chat_session_id": chat.session_id, "user_id": a_id}
        )
        return chat

    @staticmethod
    async def reserved_users(session, user_ids):
        """Все, кто живёт в активных preview-переговорах.

        Оффер резервирует пару так же, как ActiveParticipant резервирует
        чат: зарезервированный кандидат не выдаётся другим, пока
        status='requested'. Условие expires_at -- страховка на случай
        падения sweeper'а. Возвращает множество id обеих сторон.
        """
        if not user_ids:
            return set()
        rows = (
            await session.execute(
                select(PreviewOffer.user1_id, PreviewOffer.user2_id).where(
                    PreviewOffer.status == "requested",
                    PreviewOffer.expires_at > utcnow(),
                    or_(
                        PreviewOffer.user1_id.in_(user_ids),
                        PreviewOffer.user2_id.in_(user_ids),
                    ),
                )
            )
        ).all()
        reserved = set()
        for u1, u2 in rows:
            reserved.update((u1, u2))
        return reserved

    @staticmethod
    async def load_pair(s, redis, a_id, b_id):
        """Всё, что пара обязана пройти перед чатом ИЛИ preview-оффером.

        Один источник правды для match(), create_offer() и preview-согласия:
        проверка, surfacing только в одной ветке, сделала бы режим preview
        обходом фильтров и банов. Внутри: очередь/marker в Redis (live),
        ActiveParticipant, чужие живые офферы, профили с фильтрами,
        взаимные блокировки. Возвращает (users, filters) или None.
        """
        if (
            await redis.zscore("search_queue", str(a_id)) is None
            or await redis.zscore("search_queue", str(b_id)) is None
        ):
            return None
        if not await redis.exists(f"search:user:{a_id}") or not await redis.exists(
            f"search:user:{b_id}"
        ):
            return None
        if await s.scalar(
            select(ActiveParticipant)
            .where(ActiveParticipant.user_id.in_([a_id, b_id]))
            .limit(1)
        ):
            return None
        # Одна из сторон уже в живых переговорах preview с кем-то: резервация
        # живёт до expires_at или перевода строки в конечный статус.
        if await s.scalar(
            select(PreviewOffer.id)
            .where(
                PreviewOffer.status == "requested",
                PreviewOffer.expires_at > utcnow(),
                or_(
                    PreviewOffer.user1_id.in_([a_id, b_id]),
                    PreviewOffer.user2_id.in_([a_id, b_id]),
                ),
            )
            .limit(1)
        ):
            return None
        users = list(
            (
                await s.scalars(select(User).where(User.telegram_id.in_([a_id, b_id])))
            ).all()
        )
        from services.payments import available_entitlement

        filters = {}
        for uid in (a_id, b_id):
            entitlement = await available_entitlement(s, uid, ["gender_filter_1m"])
            if not entitlement:
                entitlement = await available_entitlement(
                    s, uid, ["gender_filter_one_match"]
                )
            if entitlement:
                filters[uid] = entitlement
        if len(users) != 2 or not compatible(*users, gender_access=filters):
            return None
        blocked = await s.scalar(
            select(Block).where(
                or_(
                    and_(Block.blocker_id == a_id, Block.blocked_id == b_id),
                    and_(Block.blocker_id == b_id, Block.blocked_id == a_id),
                )
            )
        )
        if blocked:
            return None
        return users, filters

    @staticmethod
    async def finalize_chat(s, a_id, b_id, users, filters):
        """Чат + участники + уведомления + обжиг платных опций.

        Единственная точка гибели gender_filter_one_match -- фактическое
        открытие чата: слепой матч и preview-согласие ведут себя тут
        одинаково. Работает внутри уже открытой транзакции s.
        """
        from services.payments import available_entitlement

        chat = Chat(
            session_id=uuid.uuid4().hex,
            user1_id=a_id,
            user2_id=b_id,
            status="active",
            started_at=utcnow(),
            last_activity_at=utcnow(),
        )
        s.add(chat)
        await s.flush()
        s.add_all(
            [
                ActiveParticipant(user_id=uid, session_id=chat.session_id)
                for uid in (a_id, b_id)
            ]
        )
        await _notify_match(s, chat, users)
        for user in users:
            entitlement = filters.get(user.telegram_id)
            if (
                entitlement
                and entitlement.kind == "gender_filter_one_match"
                and not premium_active(user)
                and user.settings_gender_filter != "a"
            ):
                entitlement.remaining_uses -= 1
            priority = await available_entitlement(
                s, user.telegram_id, ["instant_search"]
            )
            if priority and not premium_active(user):
                priority.remaining_uses -= 1
        return chat

    async def create_offer(self, a_id, b_id, redis):
        """Preview-ветка match(): вместо чата обеим сторонам уходят карточки.

        Оффер создаёт меньший telegram_id (канонизация снимает встречные
        офферы без отдельного гонщика); partial unique индекс резервирует
        пару от чужих матчей до исхода переговоров, маркеры переговорного
        окна шинуются заново, карточки и события ставятся той же
        транзакцией -- откат не оставит одинокой карточки без пары.
        Возвращает PreviewOffer или None.
        """
        if a_id == b_id:
            return None
        u1, u2 = sorted((a_id, b_id))
        async with async_session.begin() as s:
            await lock_matching(s)
            pair = await self.load_pair(s, redis, a_id, b_id)
            if pair is None:
                return None
            offer = PreviewOffer(
                user1_id=u1,
                user2_id=u2,
                expires_at=utcnow()
                + timedelta(seconds=max(10, int(config.PREVIEW_OFFER_SECONDS))),
            )
            s.add(offer)
            await s.flush()
            from services.settings import bot_value

            timeout = max(
                1, int(await bot_value("search_timeout", config.SEARCH_TIMEOUT))
            )
            await redis.set(
                f"search:user:{a_id}", str(utcnow().timestamp()), ex=timeout
            )
            await redis.set(
                f"search:user:{b_id}", str(utcnow().timestamp()), ex=timeout
            )
            from services.growth import event
            from services.notifications import enqueue

            await event(s, a_id, "preview_shown", f"preview:{offer.id}:{a_id}")
            await event(s, b_id, "preview_shown", f"preview:{offer.id}:{b_id}")
            await enqueue(
                s,
                a_id,
                "preview",
                f"preview:{offer.id}:{a_id}",
                "",
                callback=str(offer.id),
            )
            await enqueue(
                s,
                b_id,
                "preview",
                f"preview:{offer.id}:{b_id}",
                "",
                callback=str(offer.id),
            )
        return offer

    async def start_direct(self, a_id, b_id):
        """Open a chat between two friends without touching the search queue.

        Search filters (gender/age) are deliberately not applied: the two
        already chose each other. Bans, mutes, deleted profiles and blocks are.
        """
        if a_id == b_id:
            return None
        async with async_session.begin() as s:
            await lock_matching(s)
            if await s.scalar(
                select(ActiveParticipant)
                .where(ActiveParticipant.user_id.in_([a_id, b_id]))
                .limit(1)
            ):
                return None
            users = list(
                (
                    await s.scalars(
                        select(User).where(User.telegram_id.in_([a_id, b_id]))
                    )
                ).all()
            )
            if len(users) != 2:
                return None
            for user in users:
                if (
                    step_for(user) != "MENU"
                    or banned(user)
                    or muted(user)
                    or user.deleted_at
                    or user.bot_blocked_at
                ):
                    return None
            if await s.scalar(
                select(Block).where(
                    or_(
                        and_(Block.blocker_id == a_id, Block.blocked_id == b_id),
                        and_(Block.blocker_id == b_id, Block.blocked_id == a_id),
                    )
                )
            ):
                return None
            chat = Chat(
                session_id=uuid.uuid4().hex,
                user1_id=a_id,
                user2_id=b_id,
                status="active",
                started_at=utcnow(),
                last_activity_at=utcnow(),
            )
            s.add(chat)
            await s.flush()
            s.add_all(
                [
                    ActiveParticipant(user_id=uid, session_id=chat.session_id)
                    for uid in (a_id, b_id)
                ]
            )
            await _notify_match(s, chat, users)
        logging.getLogger(__name__).info(
            "chat_direct", extra={"chat_session_id": chat.session_id, "user_id": a_id}
        )
        return chat

    async def end(self, user_id, session_id=None, reason="stop"):
        async with async_session.begin() as s:
            await lock_matching(s)
            query = select(Chat).where(
                Chat.status == "active",
                or_(Chat.user1_id == user_id, Chat.user2_id == user_id),
            )
            if session_id:
                query = query.where(Chat.session_id == session_id)
            chat = await s.scalar(query.with_for_update())
            if not chat:
                return None
            now = utcnow()
            chat.status, chat.ended_at, chat.end_reason, chat.ended_by = (
                "ended",
                now,
                reason,
                user_id,
            )
            chat.duration_seconds = max(0, int((now - chat.started_at).total_seconds()))
            await s.execute(
                delete(ActiveParticipant).where(
                    ActiveParticipant.session_id == chat.session_id
                )
            )
            if reason in {"stop", "next"} and config.RECONNECT_WINDOW_MINUTES > 0:
                blocked = await s.scalar(
                    select(Block).where(
                        or_(
                            and_(
                                Block.blocker_id == chat.user1_id,
                                Block.blocked_id == chat.user2_id,
                            ),
                            and_(
                                Block.blocker_id == chat.user2_id,
                                Block.blocked_id == chat.user1_id,
                            ),
                        )
                    )
                )
                if not blocked:
                    s.add(
                        ReconnectOffer(
                            session_id=chat.session_id,
                            user1_id=chat.user1_id,
                            user2_id=chat.user2_id,
                            expires_at=now
                            + timedelta(minutes=config.RECONNECT_WINDOW_MINUTES),
                        )
                    )
            await s.execute(
                update(User)
                .where(User.telegram_id.in_([chat.user1_id, chat.user2_id]))
                .values(
                    chats_count=User.chats_count + 1,
                    total_chat_seconds=User.total_chat_seconds + chat.duration_seconds,
                )
            )
            await s.execute(
                update(RevealRequest)
                .where(
                    RevealRequest.chat_session_id == chat.session_id,
                    RevealRequest.status == "pending",
                )
                .values(status="expired", resolved_at=now)
            )
            from services.growth import completed_chat, event

            users = (
                await s.scalars(
                    select(User)
                    .where(User.telegram_id.in_([chat.user1_id, chat.user2_id]))
                    .order_by(User.telegram_id)
                    .with_for_update()
                )
            ).all()
            for user in users:
                await completed_chat(s, user)
                await event(
                    s,
                    user.telegram_id,
                    "chat_ended",
                    f"chat_ended:{chat.session_id}:{user.telegram_id}",
                )
            if reason in {"ban", "account_deleted", "unreachable", "timeout"}:
                # The actor already got a direct message from the handler for
                # unreachable; queueing one for them too would duplicate it.
                from keyboards import texts as T

                targets = [chat.user1_id, chat.user2_id]
                if reason == "unreachable":
                    targets = [uid for uid in targets if uid != user_id]
                for uid in targets:
                    from services.notifications import enqueue

                    lang = (
                        next((u.language for u in users if u.telegram_id == uid), None)
                        or "ru"
                    )
                    texts = T.tr(T.WORKER_TEXTS, lang)
                    await enqueue(
                        s,
                        uid,
                        "chat_ended",
                        f"ended:{chat.session_id}:{uid}",
                        texts["ended_timeout"]
                        if reason == "timeout"
                        else texts["ended_new"],
                        "menu",
                    )
                    if reason in {"unreachable", "timeout"}:
                        await enqueue(
                            s,
                            uid,
                            "reconnect_prompt",
                            f"reconnect:{chat.session_id}:{uid}",
                            texts["reconnect_prompt"],
                            "search:repeat",
                        )
            if reason in {"stop", "next"}:
                from services.notifications import enqueue

                from keyboards import texts as T

                partner = chat.user2_id if chat.user1_id == user_id else chat.user1_id
                # unreachable сюда не доходит: партнёру уже положен ended_new в
                # блоке выше (тот же dedupe-ключ глушил бы второй текст всегда).
                lang = (
                    next((u.language for u in users if u.telegram_id == partner), None)
                    or "ru"
                )
                await enqueue(
                    s,
                    partner,
                    "chat_after",
                    f"chat_after:{chat.session_id}:{partner}",
                    T.tr(T.CHAT_AFTER, lang)["text"],
                    chat.session_id,
                )
            if reason in {"stop", "next", "unreachable"}:
                from services.notifications import enqueue

                for uid in (chat.user1_id, chat.user2_id):
                    prompt_lang = (
                        next((u.language for u in users if u.telegram_id == uid), None)
                        or "ru"
                    )
                    await enqueue(
                        s,
                        uid,
                        "rating_prompt",
                        f"rate_prompt:{chat.session_id}:{uid}",
                        T.tr(T.WORKER_TEXTS, prompt_lang)["rating_prompt"],
                        chat.session_id,
                    )
        logging.getLogger(__name__).info(
            "chat_ended", extra={"chat_session_id": chat.session_id, "user_id": user_id}
        )
        return chat

    async def touch(self, session_id):
        async with async_session.begin() as s:
            await s.execute(
                update(Chat)
                .where(Chat.session_id == session_id, Chat.status == "active")
                .values(last_activity_at=utcnow())
            )

    async def all_active(self):
        async with async_session() as s:
            return list(
                (await s.scalars(select(Chat).where(Chat.status == "active"))).all()
            )

    async def rate(self, session_id, user_id, value):
        if value not in {-1, 1}:
            return False
        async with async_session.begin() as s:
            chat = await s.scalar(
                select(Chat)
                .where(
                    Chat.session_id == session_id,
                    Chat.status == "ended",
                    or_(Chat.user1_id == user_id, Chat.user2_id == user_id),
                )
                .with_for_update()
            )
            if not chat or await s.scalar(
                select(Rating).where(
                    Rating.chat_session_id == session_id, Rating.rater_id == user_id
                )
            ):
                return False
            partner = chat.user2_id if chat.user1_id == user_id else chat.user1_id
            s.add(
                Rating(
                    chat_session_id=session_id,
                    rater_id=user_id,
                    rated_id=partner,
                    value=value,
                )
            )
            user = await s.scalar(
                select(User).where(User.telegram_id == partner).with_for_update()
            )
            if value == 1:
                user.likes_received += 1
            else:
                user.dislikes_received += 1
            user.positive_rating_pct = round(
                100
                * user.likes_received
                / (user.likes_received + user.dislikes_received),
                1,
            )
            from services.notifications import enqueue

            from keyboards import texts as T

            partner_word = T.tr(T.WORKER_TEXTS, user.language or "ru")
            await enqueue(
                s,
                partner,
                "rating",
                f"rating:{session_id}:{user_id}",
                partner_word["rated_up"] if value == 1 else partner_word["rated_down"],
                "profile",
            )
        return True
