"""Регистрация /start: один апдейт -- одно событие, первый запуск -- одно
уведомление владельцу.

Транзакция здесь -- единица правды. Дедупликация апдейта, счётчик запусков,
строка start_events и запись очереди уведомлений фиксируются вместе: сбой на
любом шаге откатывает всё, ничего половинчатого в базе не остаётся, а
повторная доставка того же update_id не считает запуск второй раз.
"""

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import config
from models.base import async_session
from models.start import ProcessedUpdate, StartEvent
from models.user import User
from services.notifications import enqueue
from services.result import ServiceResult
from utils.time import KYIV, kyiv_day_bounds, utcnow

logger = logging.getLogger(__name__)

MISSING = "—"


def _thousands(value):
    return f"{value:,}".replace(",", " ")


async def start_totals(s, now=None):
    """Счётчики для карточки владельцу: всего и за текущие сутки по Киеву.

    Считаются запросами, а не хранимыми глобальными счётчиками -- глобальный
    счётчик расходится с таблицей после любого ручного вмешательства и врёт
    молча. Запуск -- первое нажатие /start человека, повторные заходы
    не считают: иначе «запусков» всегда больше, чем людей."""
    day_start, day_end = kyiv_day_bounds(now)
    first = StartEvent.event_type == "first_start"
    return {
        "total_users": await s.scalar(select(func.count(User.id))) or 0,
        "total_starts": await s.scalar(select(func.count(StartEvent.id)).where(first))
        or 0,
        "new_today": await s.scalar(
            select(func.count(User.id)).where(
                User.created_at >= day_start, User.created_at < day_end
            )
        )
        or 0,
        "starts_today": await s.scalar(
            select(func.count(StartEvent.id)).where(
                first,
                StartEvent.created_at >= day_start,
                StartEvent.created_at < day_end,
            )
        )
        or 0,
    }


def first_start_text(user, bot_id, totals, when=None):
    """Карточка владельцу. Простой текст без разметки: имя и username --
    пользовательские данные, и единственный безопасный способ вставить их --
    не парсить их как HTML вовсе."""
    from utils.bots import label as bot_label

    when = (when or utcnow()).astimezone(KYIV)
    username = f"@{user.username}" if user.username else MISSING
    return (
        f"🆕 НОВЫЙ ПОЛЬЗОВАТЕЛЬ\n\n"
        f"👤 Имя: {user.first_name or MISSING}\n"
        f"🔗 Username: {username}\n"
        f"🆔 Telegram ID: {user.telegram_id}\n"
        f"🕒 Первый запуск: {when:%d.%m.%Y, %H:%M} (Киев)\n"
        f"🤖 Бот: {bot_label(bot_id)}\n\n"
        f"📊 Статистика после регистрации\n"
        f"👥 Всего пользователей: {_thousands(totals['total_users'])}\n"
        f"🚀 Всего запусков: {_thousands(totals['total_starts'])}\n"
        f"🆕 Новых пользователей сегодня: {_thousands(totals['new_today'])}\n"
        f"🚀 Запусков сегодня: {_thousands(totals['starts_today'])}"
    )


def _claim_update(s, bot_id, update_id, update_type):
    if s.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return (
        insert(ProcessedUpdate)
        .values(
            bot_id=bot_id,
            update_id=update_id,
            update_type=update_type,
        )
        .on_conflict_do_nothing(index_elements=["bot_id", "update_id"])
    )


async def _register_once(
    telegram_id, bot_id, update_id, username, first_name, update_type
):
    async with async_session.begin() as s:
        # 1. Дедупликация апдейта -- раньше любых счётчиков: повторная
        #    доставка того же update_id не является вторым запуском.
        claimed = await s.execute(_claim_update(s, bot_id, update_id, update_type))
        if not claimed.rowcount:
            logger.info(
                "event=start_duplicate bot_id=%s update_id=%s", bot_id, update_id
            )
            return ServiceResult(False, "DUPLICATE")
        # 2. Пользователь. В проде строку уже создал AccessMiddleware на этом
        #    же апдейте; здесь она появляется, только если регистрацию зовут
        #    напрямую (тесты, будущие точки вызова).
        user = await s.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            user = User(
                telegram_id=telegram_id, anon_id="#" + secrets.token_hex(4).upper()
            )
            s.add(user)
            await s.flush()
        if username is not None:
            user.username = username
        if first_name is not None:
            user.first_name = first_name
        if bot_id is not None:
            user.bot_id = bot_id
        # 3. Счётчик -- под блокировкой строки: два одновременных /start
        #    одного человека не теряют единицу.
        user.start_count = (user.start_count or 0) + 1
        first = user.start_count == 1
        # 4. Событие фиксируется в одной транзакции со счётчиком, поэтому
        #    start_count всегда сходится с числом строк start_events.
        s.add(
            StartEvent(
                telegram_user_id=telegram_id,
                bot_id=bot_id,
                update_id=update_id,
                event_type="first_start" if first else "repeat_start",
            )
        )
        # 5. Уведомление владельцу -- ровно один раз за жизнь человека и
        #    только на первый запуск. Статистика идёт уже вместе с этим
        #    событием: агрегаты видят pending-insert той же сессии.
        if first and config.OWNER_ID:
            await enqueue(
                s,
                config.OWNER_ID,
                "owner_first_start",
                f"owner_first_start:{telegram_id}",
                first_start_text(user, bot_id, await start_totals(s)),
                bot_id=bot_id,
            )
        return ServiceResult(True, "FIRST" if first else "REPEAT")


async def register_start(
    telegram_id, bot_id, update_id, username=None, first_name=None, update_type="start"
):
    """Записать /start. Коды: FIRST (первый запуск), REPEAT, DUPLICATE."""
    for _ in range(5):
        try:
            return await _register_once(
                telegram_id, bot_id, update_id, username, first_name, update_type
            )
        except IntegrityError:
            # Параллельный первый запуск того же человека: строку users создал
            # соседний обработчик. Транзакция откатилась целиком -- повтор с
            # чистого листа, как в UserRepository.get_or_create.
            continue
    raise RuntimeError("Could not register start event")
