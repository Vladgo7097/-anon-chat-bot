"""User idea suggestions: storage, spam limits, statuses, owner channel.

The FSM draft lives only in aiogram state storage - nothing is written
until the author taps Send. Everything below is per-transaction and
DB-based, so limits survive restarts: no in-memory counters.
"""

from datetime import timedelta
from datetime import datetime

from sqlalchemy import func, select

import config
from keyboards import texts as T
from models.base import async_session
from models.idea import Idea
from services.result import ServiceResult
from utils.time import utcnow

# Владелец уведомляется через общий durable-воркер (services.notifications):
# сначала строка идеи, потом уведомление, одной транзакцией - если Telegram
# недоступен, идея всё равно сохранена, а карточку воркер доставит с ретраями.
OWNER_NOTICE_KIND = "owner_idea"
STATUS_NOTICE_KIND = "idea_status"
REPLY_NOTICE_KIND = "idea_reply"

# Контролируемые переходы: владелец ведёт идею по цепочке, реализованная -
# терминал. Возврат из rejected - осознанное переоткрытие через review.
IDEA_TRANSITIONS = {
    "new": {"review", "planned", "in_progress", "implemented", "rejected", "duplicate"},
    "review": {"planned", "in_progress", "implemented", "rejected", "duplicate"},
    "planned": {"in_progress", "implemented", "rejected", "duplicate", "new"},
    "in_progress": {"implemented", "rejected", "planned"},
    "implemented": set(),
    "rejected": {"new", "review"},
    "duplicate": {"new", "review"},
}

STATUSES = set(IDEA_TRANSITIONS)


def normalize_text(raw: str) -> str:
    # Регистр и лишние пробелы не делают текст идеей-другой: повтор
    # содержимого в сутки - один и тот же спам, независимо от раскладки.
    return " ".join((raw or "").split()).casefold()


def status_label(status: str, lang: str = "ru") -> str:
    return T.tr(T.IDEA_STATUSES[status], lang)


def _bot_label(bot_id):
    from utils.bots import label

    return label(bot_id)


class IdeaService:
    async def create(
        self,
        telegram_id: int,
        raw_text: str,
        bot_id: int | None,
        author_name: str,
        now: datetime | None = None,
    ) -> ServiceResult:
        """Одна заявка = одна транзакция: лимиты + вставка + уведомление.

        Лимиты читаются из базы (скользящее 24-часовое окно и 60-секундный
        кулдаун), а не из памяти: перезапуск их не сбрасывает.
        """
        text = (raw_text or "").strip()
        if not text:
            return ServiceResult(False, "EMPTY")
        size = len(text)
        if size > config.IDEA_MAX_LEN:
            return ServiceResult(
                False, "TOO_LONG", {"len": size, "max": config.IDEA_MAX_LEN}
            )
        now = now or utcnow()
        day_ago = now - timedelta(hours=24)
        normalized = normalize_text(text)
        async with async_session.begin() as s:
            recent = (
                (
                    await s.execute(
                        select(Idea)
                        .where(
                            Idea.telegram_user_id == telegram_id,
                            Idea.created_at >= day_ago,
                        )
                        .order_by(Idea.created_at)
                    )
                )
                .scalars()
                .all()
            )
            if len(recent) >= config.IDEA_MAX_PER_DAY:
                # Место освобождается через 24 часа после самого старого из
                # трёх; время показываем владельцу как момент по Киеву.
                oldest = recent[len(recent) - config.IDEA_MAX_PER_DAY]
                return ServiceResult(
                    False,
                    "RATE_LIMITED",
                    {
                        "max": config.IDEA_MAX_PER_DAY,
                        "when": oldest.created_at + timedelta(hours=24),
                    },
                )
            last = recent[-1] if recent else None
            if last is not None and last.created_at >= now - timedelta(
                seconds=config.IDEA_MIN_INTERVAL_SEC
            ):
                seconds = (
                    int(
                        (
                            last.created_at
                            + timedelta(seconds=config.IDEA_MIN_INTERVAL_SEC)
                            - now
                        ).total_seconds()
                    )
                    + 1
                )
                return ServiceResult(
                    False, "COOLDOWN", {"min": max(1, -(-seconds // 60))}
                )
            if any(normalize_text(row.text) == normalized for row in recent):
                return ServiceResult(False, "DUPLICATE_TEXT")
            idea = Idea(
                telegram_user_id=telegram_id,
                text=text,
                status="new",
                bot_id=bot_id,
                created_at=now,
                updated_at=now,
            )
            s.add(idea)
            await s.flush()
            # Уведомление владельцу уходит той же транзакцией; дедуп-ключ
            # несёт id идеи, доставку ведёт общий outbox-воркер.
            from services.notifications import enqueue

            await enqueue(
                s,
                config.OWNER_ID,
                OWNER_NOTICE_KIND,
                f"owner_idea:{idea.id}",
                T.tr(T.IDEA_OWNER_NEW, "ru").format(
                    id=idea.id,
                    author=author_name or "автор без имени",
                    author_id=telegram_id,
                    bot_label=_bot_label(bot_id),
                    text=text,
                ),
                callback=f"admin:idea:{idea.id}",
                bot_id=bot_id,
            )
        return ServiceResult(True, "CREATED", {"id": idea.id})

    async def mine(self, telegram_id: int, page: int = 1, size: int | None = None):
        """Свои идеи: чужие строки отсюда недостижимы по построению запроса."""
        size = size or config.IDEA_PAGE_SIZE
        async with async_session() as s:
            total = (
                await s.scalar(
                    select(func.count(Idea.id)).where(
                        Idea.telegram_user_id == telegram_id
                    )
                )
                or 0
            )
            pages = max(1, -(-total // size))
            page = min(max(page, 1), pages)
            rows = (
                await s.scalars(
                    select(Idea)
                    .where(Idea.telegram_user_id == telegram_id)
                    .order_by(Idea.created_at.desc())
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
        return rows, page, pages

    async def own(self, idea_id: int, telegram_id: int) -> Idea | None:
        """Строка только если она принадлежит автору -- подмена id в колбэке
        возвращает None, а не чужую карточку."""
        async with async_session() as s:
            return (
                await s.scalars(
                    select(Idea).where(
                        Idea.id == idea_id, Idea.telegram_user_id == telegram_id
                    )
                )
            ).first()

    async def admin_list(
        self, status: str = "new", page: int = 1, size: int | None = None
    ):
        """Панель: фильтр по статусу, новые первыми, paging вместо выгрузки всех."""
        size = size or config.IDEA_ADMIN_PAGE_SIZE
        async with async_session() as s:
            where = [] if status == "all" else [Idea.status == status]
            total = await s.scalar(select(func.count(Idea.id)).where(*where)) or 0
            pages = max(1, -(-total // size))
            page = min(max(page, 1), pages)
            rows = (
                await s.scalars(
                    select(Idea)
                    .where(*where)
                    .order_by(Idea.created_at.desc())
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
        return rows, page, pages

    async def get(self, idea_id: int) -> Idea | None:
        async with async_session() as s:
            return await s.get(Idea, idea_id)

    async def similar_count(self, idea_id: int, text: str) -> int:
        """Сколько людей уже просили то же самое - по тексту, точным
        совпадением. Размытый поиск оставляем админам: фальшивые «похожие»
        души легитимные идеи."""
        async with async_session() as s:
            return (
                await s.scalar(
                    select(func.count(Idea.id)).where(
                        Idea.text == text, Idea.id != idea_id
                    )
                )
                or 0
            )

    async def set_status(
        self,
        admin_id: int,
        idea_id: int,
        new_status: str,
        duplicate_of: int | None = None,
    ) -> ServiceResult:
        """Статус меняет только уполномоченный админ (гард в роутере).

        Идемпотентен: тот же статус = UNCHANGED без записи и без уведомления
        автору. Дедуп-ключ уведомления гасит и повторную обработку события.
        """
        if new_status not in STATUSES:
            return ServiceResult(False, "BAD_STATUS")
        async with async_session.begin() as s:
            idea = await s.get(Idea, idea_id)
            if idea is None:
                return ServiceResult(False, "NOT_FOUND")
            if idea.status == new_status:
                return ServiceResult(False, "UNCHANGED")
            if new_status not in IDEA_TRANSITIONS[idea.status]:
                return ServiceResult(False, "FORBID", {"from": idea.status})
            old = idea.status
            now = utcnow()
            idea.status = new_status
            idea.updated_at = now
            idea.reviewed_at = now
            idea.reviewed_by = admin_id
            if new_status == "duplicate":
                idea.duplicate_of = duplicate_of
            elif idea.duplicate_of and duplicate_of is None:
                idea.duplicate_of = None
            # Автору - смена статуса уведомлением из очереди, на его языке,
            # время постановки в момент enqueue (как концы диалогов).
            from services.notifications import enqueue
            from utils.db import user_lang

            lang = await user_lang(idea.telegram_user_id, "ru")
            await enqueue(
                s,
                idea.telegram_user_id,
                STATUS_NOTICE_KIND,
                f"idea_status:{idea.id}:{new_status}",
                T.tr(T.IDEA_STATUS_NOTIFY, lang).format(
                    id=idea.id, status=status_label(new_status, lang)
                ),
                callback=f"idea:view:{idea.id}",
            )
        return ServiceResult(True, "CHANGED", {"from": old, "id": idea_id})

    async def save_reply(
        self, admin_id: int, idea_id: int, reply_text: str
    ) -> ServiceResult:
        """Ответ владельца автору: пишется в строку идеи, доставка - через outbox.

        Работу подтверждает воркер и его ретраи, поэтому сразу после
        сохранения единственная честная формулировка - «сохранён и уйдёт».
        """
        text = (reply_text or "").strip()
        if not text or len(text) > config.IDEA_MAX_LEN:
            return ServiceResult(False, "BAD_REPLY", {"max": config.IDEA_MAX_LEN})
        async with async_session.begin() as s:
            idea = await s.get(Idea, idea_id)
            if idea is None:
                return ServiceResult(False, "NOT_FOUND")
            now = utcnow()
            idea.admin_reply = text
            idea.admin_reply_at = now
            idea.updated_at = now
            if idea.reviewed_at is None:
                idea.reviewed_at = now
                idea.reviewed_by = admin_id
            from services.notifications import enqueue
            from utils.db import user_lang

            lang = await user_lang(idea.telegram_user_id, "ru")
            await enqueue(
                s,
                idea.telegram_user_id,
                REPLY_NOTICE_KIND,
                # Ключ с моментом ответа: двойной тап в ту же секунду
                # гасится дедупом, а честная замена текста доходит автору.
                f"idea_reply:{idea.id}:{int(now.timestamp())}",
                T.tr(T.IDEA_REPLY_NOTIFY, lang).format(id=idea.id, reply=text),
                callback=f"idea:view:{idea.id}",
            )
        return ServiceResult(True, "SAVED", {"id": idea_id})
