import secrets
import config
from datetime import timedelta
from html import escape
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from models.base import async_session
from models.user import User
from utils.time import utcnow


def _heading(kind: str, bot_id) -> str:
    """Заголовок с именем бота: два бота держат разные аудитории, и без
    подписи «кто запустил» скрывает, какой именно из них растёт."""
    from utils.bots import label
    return f"<b>{escape(kind)}</b> · {escape(label(bot_id))}"


def _anon(value) -> str:
    """Профиль в code-шрифте: #ABC123 должен копироваться, а не переноситься."""
    return f"<code>#{escape(str(value or '').lstrip('#'))}</code>"


def _username(user) -> str:
    return f"@{escape(user.username)}" if user.username else "не указан"


def _first_name(user) -> str:
    # first_name -- пользовательская строка без ограничений Telegram: туда
    # пролезают '<' и '&', а сообщение уходит с parse_mode="HTML". Без escape
    # одно имя превращает уведомление в мусор.
    return escape(user.first_name) if user.first_name else "не указано"


class UserRepository:
    async def get(self, telegram_id: int):
        async with async_session() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def _owner_cap(self, session, key, default):
        """Сколько уведомлений этого вида можно показать поштучно за сутки.

        Читается через ту же сессию: вложенная взяла бы второй пул под
        открытой транзакцией.
        """
        from models.operations import BotSetting
        setting = await session.get(BotSetting, key)
        return setting.value_json["value"] if setting else default

    async def _today_shown(self, session, kind, bot_id):
        """Сколько уже показано сегодня -- своим бота, а не суммарно."""
        from models.operations import Notification
        day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        scope = Notification.bot_id == bot_id if bot_id is not None else Notification.bot_id.is_(None)
        return await session.scalar(select(func.count(Notification.id)).where(
            Notification.type == kind, scope,
            Notification.scheduled_at >= day_start)) or 0

    async def notify_owner_launch(self, session, user, telegram_id, switched=False):
        """Каждый, кто запустил бота, -- отдельным сообщением владельцу.

        Всплеск трафика -- лучший момент, а не авария: однажды он залил чат
        1148 сообщениями, поэтому лимит остался, но считается на каждого бота
        отдельно. Реклама, зашедшая во второй бот, не съедает квоту первого,
        и видно, какой именно бот растёт. За потолком подхватывает часовая
        сводка, уже тоже разложенная по ботам.

        `switched` -- тот же человек, открывший второй бот: без отдельного
        сообщения наблюдение «кто запускает первый, кто второй» свелось бы к
        первой регистрации.
        """
        if not config.OWNER_ID:
            return
        limit = await self._owner_cap(session, "owner_notice_limit", 30)
        if await self._today_shown(session, "owner_new_user", user.bot_id) >= limit:
            return
        from services.notifications import enqueue
        total = await session.scalar(select(func.count(User.id)))
        kind = "Запуск · другой бот" if switched else "Запуск"
        # Дедуп держит бота в ключе: один человек, открывший оба бота, --
        # это ровно то наблюдение, ради которого их два.
        await enqueue(session, config.OWNER_ID, "owner_new_user",
                      f"owner_new_user:{telegram_id}:{user.bot_id or 0}:{config.OWNER_ID}",
                      f"{_heading(kind, user.bot_id)}\n"
                      f"Профиль: {_anon(user.anon_id)}\n"
                      f"Телеграм: {telegram_id}\n"
                      f"Юзернейм: {_username(user)}\n"
                      f"Имя: {_first_name(user)}\n"
                      f"Всего пользователей: {total}",
                      f"admin:user_card:{telegram_id}", bot_id=user.bot_id)

    async def notify_owner_signup(self, session, user):
        """Тот, кто дошёл до меню, -- с профилем и источником трафика.

        Запуск и «остался» -- разные сигналы: первый показывает приток, второй
        -- качество. Приходя сюда, человек уже заполнил пол, возраст и язык,
        а deep-link уже записал referrer_id и start_source, поэтому источник
        наконец виден -- в момент запуска его ещё не существует.
        """
        if not config.OWNER_ID:
            return
        limit = await self._owner_cap(session, "owner_activation_limit", 25)
        if await self._today_shown(session, "owner_activated", user.bot_id) >= limit:
            return
        from models.operations import AnalyticsEvent
        from services.notifications import enqueue
        row = await session.scalar(select(AnalyticsEvent.dedupe_key).where(
            AnalyticsEvent.event == "start_source",
            AnalyticsEvent.user_id == user.telegram_id).limit(1))
        # Ключ хранится как start_source:{telegram_id}:{source}, и source --
        # единственное, что интересно.
        source = (row.rsplit(":", 1)[-1] if row else "none") or "none"
        if source == "none":
            source_label = "прямой заход"
        elif source.startswith("ref_"):
            # Реферальный payload -- это ref_ плюс 32 символа кода: для
            # владельца важен сам факт, а не hex под ним.
            source_label = "реферальная ссылка"
        else:
            source_label = escape(source)
        invited = "нет"
        if user.referrer_id:
            by = await session.scalar(select(User.anon_id).where(
                User.telegram_id == user.referrer_id))
            invited = _anon(by) if by else str(user.referrer_id)
        reached = await session.scalar(select(func.count(User.id)).where(
            User.onboarding_completed.is_(True))) or 0
        gender = {"male": "м", "female": "ж"}.get(user.gender, "?")
        await enqueue(session, config.OWNER_ID, "owner_activated",
                      f"owner_activated:{user.telegram_id}:{user.bot_id or 0}:{config.OWNER_ID}",
                      f"{_heading('Дошёл до меню', user.bot_id)}\n"
                      f"Профиль: {_anon(user.anon_id)}\n"
                      f"Телеграм: {user.telegram_id} · {_username(user)}\n"
                      f"Имя: {_first_name(user)} · {user.age or '?'} · {gender}\n"
                      f"Язык: {escape(user.language or '?')}\n"
                      f"Пришёл: {source_label} · привёл {invited}\n"
                      f"Всего дошедших: {reached}",
                      f"admin:user_card:{user.telegram_id}", bot_id=user.bot_id)

    async def get_or_create(self, telegram_id: int, username=None, first_name=None, bot_id=None):
        for _ in range(10):
            try:
                is_new = False
                async with async_session.begin() as session:
                    query = select(User).where(User.telegram_id == telegram_id)
                    user = await session.scalar(query)
                    now = utcnow()
                    if (user is not None and user.username == username
                            and (first_name is None or user.first_name == first_name)
                            # A user switching between the two bots must fall through
                            # to the write path below, or their bot_id would stay stale.
                            and (bot_id is None or user.bot_id == bot_id)
                            and user.bot_blocked_at is None and user.last_online
                            and user.last_online >= now-timedelta(seconds=60)):
                        return user, False
                    if user is not None:
                        user = await session.scalar(query.with_for_update().execution_options(populate_existing=True))
                    if user is None:
                        user = User(telegram_id=telegram_id, anon_id="#" + secrets.token_hex(4).upper())
                        session.add(user)
                        is_new = True
                    previous_bot = user.bot_id
                    user.username = username
                    if first_name is not None:
                        user.first_name = first_name
                    if bot_id is not None:
                        user.bot_id = bot_id
                    user.last_online = now
                    user.bot_blocked_at = None
                    await session.flush()
                    if config.OWNER_ID:
                        if is_new:
                            await self.notify_owner_launch(session, user, telegram_id)
                        elif bot_id is not None and previous_bot != bot_id:
                            # Человек вернулся уже через другого бота. Это
                            # запуск второго бота, и без отдельного уведомления
                            # наблюдение «кто запускает первый, кто второй»
                            # свелось бы к первой регистрации человека.
                            # Дедуп стоит на паре (человек, бот), поэтому
                            # перекидывание туда-сюда флудом не станет.
                            await self.notify_owner_launch(session, user, telegram_id,
                                                           switched=True)
                return user, is_new
            except IntegrityError:
                # Concurrent /start or anon_id collision: retry in a new transaction.
                continue
        raise RuntimeError("Could not allocate anonymous profile")

    async def transition(self, telegram_id, operation):
        async with async_session.begin() as session:
            from repositories.chats import lock_matching
            await lock_matching(session)
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            return await operation(user, session)
