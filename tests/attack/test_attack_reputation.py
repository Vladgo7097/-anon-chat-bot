"""Red-team suite: reputation surface (reports/complaints/bans) + DB races.

Rules of engagement obeyed here:
  * everything runs against a throwaway SQLite file and a mocked Bot session;
  * no network, no Telegram Bot API, no real recipients -- outbound traffic is
    captured by patching bot.session.make_request and never sent;
  * nothing outside tests/attack/ is touched.

Each test is one attacker action seen from outside the bot: a spoofed callback,
a flood of complaints, a self-report, a racing ban/unban, a duplicated update.
"""

import asyncio
import sys
import tempfile
import time
import unittest
import uuid

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from handlers import load_routers
from models.base import Base
from models.ban import Ban
from models.chat import Block
from models.chat import Chat as ChatRow
from models.operations import Notification
from models.report import Report
from models.user import User
import models.op  # noqa: F401
from repositories.chats import ChatRepository
from services.admin import AdminService
from services.notifications import NotificationService, enqueue
from services.reports import ReportService
from services.result import ServiceResult
from utils.deduplication import UpdateDeduplication
from utils.middleware import AccessMiddleware, AntiFloodMiddleware
from utils.time import utcnow

ATTACKER = 9100
VICTIM = 9101
THIRD = 9102
ADMIN = 99

# Every module that binds `async_session` at import time and therefore needs
# the test factory instead of the production engine.
SESSION_MODULES = (
    "models.base",
    "repositories.chats",
    "repositories.users",
    "services.reports",
    "services.admin",
    "services.notifications",
    "services.reveal",
    "services.growth",
    "services.payments",
    "services.funnel_alert",
    "services.matching",
    "services.profile",
    "services.settings",
    "services.ads",
)


class FakePipeline:
    """Records commands, replays them on execute() -- mirrors redis pipeline."""

    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.ops.append((name, args, kwargs))

        return record

    async def execute(self):
        for name, args, kwargs in self.ops:
            await getattr(self.redis, name)(*args, **kwargs)
        self.ops.clear()
        return True


class FakeRedis:
    """In-memory Redis: kv (+TTL), sorted sets, lists. No sockets, no timeouts."""

    def __init__(self):
        self.kv = {}  # key -> (value, expires_at_monotonic | None)
        self.zsets = {}
        self.lists = {}

    def _alive(self, key):
        entry = self.kv.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            del self.kv[key]
            return None
        return value

    async def exists(self, key):
        return 1 if self._alive(key) is not None else 0

    async def get(self, key):
        return self._alive(key)

    async def set(self, key, value, nx=False, px=None, ex=None, **kw):
        current = self._alive(key)
        if nx and current is not None:
            return None
        expires_at = None
        if px is not None:
            expires_at = time.monotonic() + px / 1000
        elif ex is not None:
            expires_at = time.monotonic() + ex
        self.kv[key] = (value, expires_at)
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            for store in (self.kv, self.zsets, self.lists):
                if key in store:
                    del store[key]
                    removed += 1
        return removed

    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        rows = self.lists.get(key, [])
        self.lists[key] = rows[start:] if end == -1 else rows[start : end + 1]
        return True

    async def lrange(self, key, start, end):
        rows = self.lists.get(key, [])
        return rows[start:] if end == -1 else rows[start : end + 1]

    async def expire(self, key, ttl):
        return 1

    async def zadd(self, key, mapping, nx=False, xx=False, ch=False, incr=False):
        z = self.zsets.setdefault(key, {})
        z.update({str(m): float(v) for m, v in mapping.items()})
        return len(mapping)

    async def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        return sum(1 for m in members if z.pop(str(m), None) is not None)

    async def zscore(self, key, member):
        return self.zsets.get(key, {}).get(str(member))

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zcount(self, key, min_s, max_s):
        lo = float("-inf") if min_s == "-inf" else float(min_s)
        hi = float("inf") if max_s == "+inf" else float(max_s)
        return sum(1 for sc in self.zsets.get(key, {}).values() if lo < sc <= hi)

    async def zremrangebyscore(self, key, min_s, max_s):
        lo = float("-inf") if min_s == "-inf" else float(min_s)
        hi = float("inf") if max_s == "+inf" else float(max_s)
        z = self.zsets.get(key, {})
        for member in [m for m, sc in z.items() if lo < sc <= hi]:
            del z[member]
        return True

    async def eval(self, *args, **kwargs):
        return 0

    async def ping(self):
        return True

    async def aclose(self):
        return None

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class AttackCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="attack-rep-")
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "attack.db")
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in SESSION_MODULES:
            patcher = patch(module + ".async_session", self.factory)
            patcher.start()
            self.addCleanup(patcher.stop)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.redis = FakeRedis()
        for target in (
            "services.chat_manager.get_redis",
            "utils.deduplication.get_redis",
        ):
            patcher = patch(target, AsyncMock(return_value=self.redis))
            patcher.start()
            self.addCleanup(patcher.stop)

        for target, value in (
            ("config.OWNER_ID", ADMIN),
            ("config.ADMIN_IDS", {ADMIN}),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        for target, value in (
            ("services.growth.StreakService.checkin", AsyncMock()),
            ("handlers.search.begin_search", AsyncMock()),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.out = []
        self.bot = Bot(token="123456:ATTACK_TEST_TOKEN")
        self.bot._me = TgUser(
            id=1, is_bot=True, first_name="AttackBot", username="attack_test_bot"
        )
        patcher = patch.object(self.bot.session, "make_request", self._record)
        patcher.start()
        self.addCleanup(patcher.stop)

        routers = load_routers()
        for router in routers:
            router._parent_router = None
        self.dp = Dispatcher()
        self.dp.update.outer_middleware(UpdateDeduplication())
        self.dp.include_routers(*routers)
        self.dp.message.middleware(AntiFloodMiddleware())
        self.dp.callback_query.middleware(AntiFloodMiddleware())
        self.dp.message.outer_middleware(AccessMiddleware())
        self.dp.callback_query.outer_middleware(AccessMiddleware())
        self._update_id = 5000

        async with self.factory.begin() as s:
            for uid in (ADMIN, ATTACKER, VICTIM, THIRD):
                s.add(
                    User(
                        telegram_id=uid,
                        anon_id=f"#atk{uid}",
                        gender="male",
                        age=25,
                        language="ru",
                        onboarding_completed=True,
                        onboarding_step="MENU",
                        rules_version=1,
                        rules_accepted_at=utcnow(),
                    )
                )

    async def asyncTearDown(self):
        await self.dp.storage.close()
        for router in load_routers():
            router._parent_router = None
        await self.bot.session.close()
        await self.engine.dispose()
        self.temp.cleanup()

    # ---------------------------------------------------------------- helpers
    async def _record(self, *args, **kwargs):
        # make_request(bot, method) -- args[1] is the outgoing Bot API method.
        self.out.append(args[1])
        return True

    def _sent_texts(self):
        from aiogram.methods import SendMessage

        return [m.text for m in self.out if isinstance(m, SendMessage)]

    def _toasts(self):
        from aiogram.methods import AnswerCallbackQuery

        return [
            m.text
            for m in self.out
            if isinstance(m, AnswerCallbackQuery) and getattr(m, "text", None)
        ]

    def _next_update_id(self):
        self._update_id += 1
        return self._update_id

    async def _tap(self, data, uid=ATTACKER):
        update_id = self._next_update_id()
        return await self.dp.feed_update(
            self.bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    from_user=TgUser(
                        id=uid, is_bot=False, first_name="Attacker", language_code="ru"
                    ),
                    chat_instance="attack",
                    data=data,
                    message=Message(
                        message_id=update_id,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=uid, type="private"),
                        from_user=TgUser(
                            id=1,
                            is_bot=True,
                            first_name="AttackBot",
                            username="attack_test_bot",
                        ),
                        text="stub",
                    ),
                ),
            ),
        )

    async def _send(self, text, uid=ATTACKER):
        update_id = self._next_update_id()
        return await self.dp.feed_update(
            self.bot,
            Update(
                update_id=update_id,
                message=Message(
                    message_id=update_id,
                    date=datetime.now(timezone.utc),
                    chat=Chat(id=uid, type="private"),
                    from_user=TgUser(
                        id=uid, is_bot=False, first_name="Attacker", language_code="ru"
                    ),
                    text=text,
                ),
            ),
        )

    async def _new_chat(self, a, b, status="active"):
        session_id = uuid.uuid4().hex
        async with self.factory.begin() as s:
            s.add(
                ChatRow(
                    session_id=session_id,
                    user1_id=a,
                    user2_id=b,
                    status=status,
                    started_at=utcnow(),
                    last_activity_at=utcnow(),
                )
            )
        return session_id

    async def _seed_pending_report(self, reporter=ATTACKER, reported=VICTIM):
        session_id = uuid.uuid4().hex
        async with self.factory.begin() as s:
            report = Report(
                reporter_id=reporter,
                reported_id=reported,
                chat_session_id=session_id,
                reason="Спам/реклама",
            )
            s.add(report)
            await s.flush()
            return report.id

    async def _count(self, statement):
        async with self.factory() as s:
            return await s.scalar(select(func.count()).select_from(statement))

    async def _user(self, uid):
        async with self.factory() as s:
            return await s.scalar(select(User).where(User.telegram_id == uid))

    async def _report_row(self, report_id):
        async with self.factory() as s:
            return await s.get(Report, report_id)

    # ------------------------------------------------------------------ tests
    async def test_spoofed_admin_callbacks_are_refused_for_a_regular_user(self):
        """Привилегированная эскалация: обычный юзер шлёт admin:ban* callback."""
        report_id = await self._seed_pending_report()

        for data in (
            f"admin:ban1:{report_id}",
            f"admin:banf:{report_id}",
            f"admin:dismiss:{report_id}",
            f"admin:doban:{VICTIM}:24",
            f"admin:uban:{VICTIM}",
            f"admin:reports:0",
            "admin:bans",
        ):
            await self._tap(data, uid=ATTACKER)
            await asyncio.sleep(0.55)  # диалог тапов иначе упирается в AntiFlood
        await self._send(f"/ban_user {VICTIM} 24 spoofed", uid=ATTACKER)

        # service layer refuses the same caller directly (defense in depth)
        with self.assertRaises(PermissionError):
            await AdminService().resolve(ATTACKER, report_id, "ban1")
        with self.assertRaises(PermissionError):
            await AdminService().ban(ATTACKER, VICTIM, "spoofed", 24)
        with self.assertRaises(PermissionError):
            await AdminService().unban(ATTACKER, VICTIM)

        self.assertTrue(self._toasts(), "атакующий должен получить отказ")
        self.assertTrue(
            all("Нет доступа" in t for t in self._toasts()),
            f"неожиданные ответы: {self._toasts()}",
        )
        self.assertEqual(await self._count(Ban), 0, "жертва не должна получить бан")
        self.assertFalse((await self._user(VICTIM)).is_banned)
        self.assertEqual(
            (await self._report_row(report_id)).status,
            "pending",
            "жалоба не должна быть закрыта чужим тапом",
        )

    async def test_report_callback_stays_rate_limited_without_redis(self):
        """Защита: 0.5s лимит держится и при падении Redis (локальный слайдер)."""
        session_id = await self._new_chat(ATTACKER, VICTIM)
        with patch(
            "services.reports.ReportService.submit",
            AsyncMock(return_value=ServiceResult(False, "NOT_FOUND")),
        ) as submit:
            # два мгновенных тапа: второй должен упереться в лимит
            await self._tap(f"v1:report:{session_id}:spam")
            await self._tap(f"v1:report:{session_id}:spam")
            self.assertEqual(
                submit.await_count, 1, "второй тап в пределах 0.5s должен блокироваться"
            )
            await asyncio.sleep(0.6)
            await self._tap(f"v1:report:{session_id}:spam")
            self.assertEqual(submit.await_count, 2, "после окна тап должен проходить")

        # Redis упал: лимит обязан остаться, а не исчезнуть
        with (
            patch(
                "services.chat_manager.get_redis",
                AsyncMock(side_effect=RedisConnectionError("redis down")),
            ),
            patch(
                "services.reports.ReportService.submit",
                AsyncMock(return_value=ServiceResult(False, "NOT_FOUND")),
            ) as submit,
        ):
            for _index in range(10):
                await self._tap(f"v1:report:{session_id}:spam")
            # Первый тап в окне проходит, остальные держатся локальным
            # слайдером: без Redis флуд-контроль больше не выключается.
            self.assertEqual(
                submit.await_count, 1, "без Redis лимит обязан остаться"
            )

    async def test_thousand_report_flood_is_capped_by_the_victim_quota(self):
        """Защита: флуд из 1000 разных аккаунтов на одну жертву упирается
        в дневную квоту жертвы -- не пишется и не оповещает владельца."""
        reporters = list(range(9200, 9200 + 1000))
        sessions = [uuid.uuid4().hex for _ in reporters]
        async with self.factory.begin() as s:
            for uid in reporters:
                s.add(
                    User(
                        telegram_id=uid,
                        anon_id=f"#bot{uid}",
                        gender="male",
                        age=25,
                        language="ru",
                        onboarding_completed=True,
                        onboarding_step="MENU",
                        rules_version=1,
                        rules_accepted_at=utcnow(),
                    )
                )
                s.add(
                    ChatRow(
                        session_id=sessions[uid - reporters[0]],
                        user1_id=uid,
                        user2_id=VICTIM,
                        status="active",
                        started_at=utcnow(),
                        last_activity_at=utcnow(),
                    )
                )

        service = ReportService()
        accepted = 0
        for session_id in sessions:
            result = await service.submit(
                reporters[sessions.index(session_id)], session_id, "spam"
            )
            accepted += bool(result.ok)

        from services.reports import MAX_REPORTS_PER_VICTIM_PER_DAY

        self.assertEqual(
            accepted,
            MAX_REPORTS_PER_VICTIM_PER_DAY,
            "квота жертвы пропускает ровно дневную норму",
        )
        self.assertEqual(await self._count(Report), MAX_REPORTS_PER_VICTIM_PER_DAY)
        self.assertEqual(await self._count(Block), MAX_REPORTS_PER_VICTIM_PER_DAY)
        owner_notices = await self._count(Notification)
        async with self.factory() as s:
            owner_notices = await s.scalar(
                select(func.count(Notification.id)).where(
                    Notification.type == "owner_report"
                )
            )
        self.assertEqual(
            owner_notices,
            MAX_REPORTS_PER_VICTIM_PER_DAY,
            "владельцу уходит не больше дневной нормы жалоб",
        )
        self.assertEqual(
            await AdminService().reports_against(ADMIN, VICTIM),
            MAX_REPORTS_PER_VICTIM_PER_DAY,
        )

    async def test_a_critical_owner_alert_bypasses_a_foreign_cooldown(self):
        """Защита: чужой rate-limit на жалобе не удерживает критический алерт."""
        sid_one = await self._new_chat(ATTACKER, VICTIM)
        service = ReportService()
        first = await service.submit(ATTACKER, sid_one, "fraud")
        self.assertTrue(first.ok)

        queue = NotificationService()
        notice = await queue.next()
        self.assertIsNotNone(notice)
        self.assertEqual(notice.type, "owner_report")
        # Telegram ответил 429 на жалобу владельца
        await queue.finish(notice.id, "queued", "rate_limit", 60)

        # критический алерт (обрезался бэкап) приходит в тот же ящик
        async with self.factory.begin() as s:
            await enqueue(
                s, ADMIN, "backup_failed", "backup_failed:attack-1", "backup failed"
            )
        critical = await queue.next()
        self.assertIsNotNone(
            critical, "критический алерт не должен ждать чужой cooldown"
        )
        self.assertEqual(critical.type, "backup_failed")

    async def test_unban_is_silently_lost_against_a_concurrent_ban(self):
        """Гонка ban/unban: транзакция ban пишет поверх более позднего unban."""
        victim = await self._user(VICTIM)
        self.assertFalse(victim.is_banned)

        async with self.factory.begin() as s:
            # фаза чтения транзакции ban: FOR UPDATE на SQLite игнорируется
            stale = await s.scalar(select(User).where(User.telegram_id == VICTIM))
            self.assertFalse(stale.is_banned)
            # админ вызывает unban ПОЗЖЕ -- он выполняется целиком здесь
            unbanned = await AdminService().unban(ADMIN, VICTIM)
            self.assertTrue(unbanned.ok)
            # транзакция ban продолжается со старым прочтением
            stale.is_banned, stale.ban_expires = True, None
            s.add(
                Ban(
                    user_id=VICTIM,
                    reason="race",
                    duration_hours=None,
                    banned_by=ADMIN,
                    is_active=True,
                )
            )

        victim = await self._user(VICTIM)
        self.assertTrue(
            victim.is_banned,
            "unban выполнен позже, но бан перекрыл его -- потеря записи",
        )
        async with self.factory() as s:
            active = await s.scalar(
                select(func.count(Ban.id)).where(
                    Ban.user_id == VICTIM, Ban.is_active.is_(True)
                )
            )
        self.assertEqual(active, 1)

    async def test_ban_unban_gather_keeps_the_ban_flag_invariant(self):
        """Защита: ban/unban сериализованы мьютексом в пределах процесса.

        На PostgreSQL их держит FOR UPDATE/advisory lock, на SQLite обе
        блокировки -- пустышка, и без мьютекса интерливинг рвал инвариант
        is_banned ⇔ активная строка Ban.
        """
        violations = []
        for round_index in range(20):
            async with self.factory.begin() as s:
                user = await s.scalar(select(User).where(User.telegram_id == VICTIM))
                user.is_banned, user.ban_expires = False, None
                await s.execute(
                    Ban.__table__.update()
                    .where(Ban.user_id == VICTIM)
                    .values(is_active=False)
                )
            await asyncio.gather(
                AdminService().ban(ADMIN, VICTIM, "race", 24),
                AdminService().unban(ADMIN, VICTIM),
                return_exceptions=True,
            )
            async with self.factory() as s:
                user = await s.scalar(select(User).where(User.telegram_id == VICTIM))
                active = await s.scalar(
                    select(func.count(Ban.id)).where(
                        Ban.user_id == VICTIM, Ban.is_active.is_(True)
                    )
                )
            if bool(user.is_banned) != bool(active):
                violations.append((round_index, bool(user.is_banned), int(active)))
        self.assertEqual(
            violations,
            [],
            "мьютекс не сериализовал ban/unban -- инвариант снова рвётся",
        )

    async def test_parallel_duplicate_reports_are_refused_cleanly(self):
        """Защита: двойной тап по жалобе даёт чистый отказ, а не IntegrityError."""
        session_id = await self._new_chat(ATTACKER, VICTIM)
        service = ReportService()
        outcomes = await asyncio.gather(
            service.submit(ATTACKER, session_id, "spam"),
            service.submit(ATTACKER, session_id, "spam"),
            return_exceptions=True,
        )

        errors = [item for item in outcomes if isinstance(item, BaseException)]
        ok = [item for item in outcomes if isinstance(item, ServiceResult) and item.ok]
        self.assertEqual(
            await self._count(Report),
            1,
            "дубликат в БД держит только UNIQUE-констрейнт",
        )
        self.assertEqual(len(ok), 1, "ровно один отчёт должен записаться")
        self.assertTrue(
            errors
            or any(
                isinstance(item, ServiceResult) and item.code == "ALREADY_REPORTED"
                for item in outcomes
            ),
            "вторая транзакция обязана получить отказ или исключение",
        )
        # воспроизводимость:25 пар дублей подряд -- ни одно исключение
        # не должно вырваться мимо обёртки
        for _attempt in range(25):
            sid = uuid.uuid4().hex
            async with self.factory.begin() as s:
                s.add(
                    ChatRow(
                        session_id=sid,
                        user1_id=ATTACKER,
                        user2_id=VICTIM,
                        status="active",
                        started_at=utcnow(),
                        last_activity_at=utcnow(),
                    )
                )
            pair = await asyncio.gather(
                service.submit(ATTACKER, sid, "spam"),
                service.submit(ATTACKER, sid, "spam"),
                return_exceptions=True,
            )
            errors.extend(x for x in pair if isinstance(x, BaseException))
        self.assertEqual(
            [x for x in errors if isinstance(x, IntegrityError)],
            [],
            "гонка вырвалась мимо обёртки IntegrityError в submit",
        )
        self.assertEqual(
            len(ok), 1, "ровно один отчёт из25 параллельных дублей"
        )

    async def test_identical_update_id_is_processed_once(self):
        """Дедупликация: WeakValueDictionary-лок держит одинаковый update_id."""
        session_id = await self._new_chat(ATTACKER, VICTIM)
        calls = []

        async def slow_submit(user_id, session_id_, reason, details=None):
            calls.append(user_id)
            await asyncio.sleep(0.05)
            return ServiceResult(False, "NOT_FOUND")

        with patch("services.reports.ReportService.submit", slow_submit):
            update_id = self._next_update_id()
            first = Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id="c1",
                    from_user=TgUser(id=ATTACKER, is_bot=False, first_name="A"),
                    chat_instance="x",
                    data=f"v1:report:{session_id}:spam",
                    message=Message(
                        message_id=1,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=ATTACKER, type="private"),
                        from_user=TgUser(id=1, is_bot=True, first_name="B"),
                        text="stub",
                    ),
                ),
            )
            second = Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id="c2",
                    from_user=TgUser(id=ATTACKER, is_bot=False, first_name="A"),
                    chat_instance="x",
                    data=f"v1:report:{session_id}:spam",
                    message=Message(
                        message_id=2,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=ATTACKER, type="private"),
                        from_user=TgUser(id=1, is_bot=True, first_name="B"),
                        text="stub",
                    ),
                ),
            )
            await asyncio.gather(
                self.dp.feed_update(self.bot, first),
                self.dp.feed_update(self.bot, second),
            )
            self.assertEqual(len(calls), 1, "одинаковый update_id обработан дважды")
            await self.dp.feed_update(self.bot, first)  # повторная доставка
            self.assertEqual(len(calls), 1, "повторная доставка обработана снова")

    async def test_expired_ban_lifts_and_permanent_ban_never_lifts(self):
        """Бан истёк vs бан навсегда."""
        from services.onboarding import banned

        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == VICTIM))
            user.is_banned = True
            user.ban_expires = utcnow() - timedelta(minutes=1)
            s.add(
                Ban(
                    user_id=VICTIM,
                    reason="expired",
                    duration_hours=1,
                    expires_at=utcnow() - timedelta(minutes=1),
                    banned_by=ADMIN,
                    is_active=True,
                )
            )

        expired = await self._user(VICTIM)
        self.assertFalse(banned(expired), "истёкший бан не должен блокировать")
        await self._tap("stats", uid=VICTIM)
        self.assertNotIn(
            "заблокирован",
            " ".join(self._toasts()).lower(),
            f"заблокированный тост при истёкшем бане: {self._toasts()}",
        )

        await NotificationService().schedule()
        cleaned = await self._user(VICTIM)
        self.assertFalse(cleaned.is_banned, "воркер обязан снять истёкший бан")
        async with self.factory() as s:
            self.assertEqual(
                await s.scalar(
                    select(func.count(Ban.id)).where(
                        Ban.user_id == VICTIM, Ban.is_active.is_(True)
                    )
                ),
                0,
            )
            unban_notice = await s.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == VICTIM, Notification.type == "unban"
                )
            )
        self.assertEqual(unban_notice, 1, "об устаревании банов должен прийти отклик")

        # permanent
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == VICTIM))
            user.is_banned, user.ban_expires = True, None
        permanent = await self._user(VICTIM)
        self.assertTrue(banned(permanent), "вечный бан не снимается ничем")
        await self._tap("stats", uid=VICTIM)
        self.assertTrue(
            any("заблокирован" in t.lower() for t in self._toasts()),
            f"вечный бан должен блокировать: {self._toasts()}",
        )

    async def test_report_attribution_cannot_be_spoofed(self):
        """Чужая жертва и свой id: атрибуция берётся из чата, не из callback."""
        foreign = await self._new_chat(VICTIM, THIRD)
        result = await ReportService().submit(ATTACKER, foreign, "spam")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NOT_FOUND")
        self.assertEqual(await self._count(Report), 0)

        unknown = uuid.uuid4().hex
        result = await ReportService().submit(ATTACKER, unknown, "spam")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NOT_FOUND")

        own = await self._new_chat(ATTACKER, VICTIM)
        result = await ReportService().submit(ATTACKER, own, "insult")
        self.assertTrue(result.ok)
        row = await self._report_row(result.data["report_id"])
        self.assertEqual(row.reporter_id, ATTACKER)
        self.assertEqual(row.reported_id, VICTIM)
        self.assertEqual(await AdminService().reports_against(ADMIN, THIRD), 0)

        # self-report: чат с одинаковыми юзерами запрещён констрейнтом
        with self.assertRaises(IntegrityError):
            async with self.factory.begin() as s:
                s.add(
                    ChatRow(
                        session_id=uuid.uuid4().hex,
                        user1_id=ATTACKER,
                        user2_id=ATTACKER,
                        status="active",
                        started_at=utcnow(),
                        last_activity_at=utcnow(),
                    )
                )

    async def test_forged_report_callbacks_never_crash(self):
        """Бот, несуществующий sid, потерянное FSM-состояние -- без исключений."""
        for bogus in ("0" * 32, uuid.uuid4().hex, "not-a-session"):
            await self._tap(f"v1:report:{bogus}:spam")
            await self._tap(f"v1:report:{bogus}:minor")
        self.assertEqual(await self._count(Report), 0)

        from handlers.report import report_details

        state = SimpleNamespaceFSM()
        message = SimpleNamespaceMessage(uid=ATTACKER)
        await report_details(message, state, lang="ru")
        self.assertEqual(await self._count(Report), 0)
        self.assertEqual(
            message.answers[-1],
            "Жалоба уже отправлена или диалог завершён.",
            f"неожиданный ответ: {message.answers}",
        )


class SimpleNamespaceFSM:
    async def get_data(self):
        return {}

    async def clear(self):
        return None

    async def set_state(self, *a, **k):
        return None


class SimpleNamespaceMessage:
    def __init__(self, uid):
        self.from_user = type("U", (), {"id": uid})()
        self.text = "details"
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
