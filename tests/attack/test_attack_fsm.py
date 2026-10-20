"""Red-team harness: FSM states and user-input handling.

Local only: SQLite factory + in-memory fake Redis + a Bot whose session is an
AsyncMock. No network, no real Telegram, no touch of the production project
files outside tests/attack/.
"""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from handlers import load_routers
from models.base import Base
from models.chat import ActiveParticipant, Chat as ChatModel
from models.user import User as UserModel
import models.chat  # noqa: F401
import models.operations  # noqa: F401
import models.payment  # noqa: F401
import models.report  # noqa: F401
import models.ban  # noqa: F401
import models.op  # noqa: F401

from utils.middleware import AccessMiddleware, AntiFloodMiddleware


_HTML_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
    "tg-spoiler",
    "blockquote",
    "a",
    "tg-emoji",
}


def telegram_would_reject_html(text) -> bool:
    """Model the Bot API's HTML parse check.

    Any `<` that does not open a balanced entity makes Telegram answer 400
    "can't parse entities" and drop the whole message. Only the tags the API
    itself knows are balanced here; everything else counts as broken, which is
    what the real parser does with user-supplied markup.
    """
    if not isinstance(text, str) or "<" not in text:
        return False
    stack = []
    i = 0
    while i < len(text):
        if text[i] != "<":
            i += 1
            continue
        j = text.find(">", i)
        if j == -1:
            return True
        raw = text[i + 1 : j]
        closing = raw.startswith("/")
        body = raw[1:] if closing else raw
        name = body.strip().lower().split(maxsplit=1)[0] if body.strip() else ""
        if name and name[0].isalpha():
            if name in _HTML_TAGS or name.startswith("tg-"):
                if closing:
                    if not stack or stack[-1] != name:
                        return True
                    stack.pop()
                else:
                    stack.append(name)
            else:
                return True
        elif raw.startswith("/"):
            return True
        else:
            # "a < b", "I <3 cats": Telegram treats any stray `<` as markup.
            return True
        i = j + 1
    return bool(stack)


# --------------------------------------------------------------------------- #
# Fake Redis -- the FSM/индексная обвязка боевого бота живёт в Redis, и без
# неё половина атак просто не доедет до обработчика.
# --------------------------------------------------------------------------- #
class _Pipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return record

    async def execute(self):
        out = [
            await getattr(self._redis, name)(*args, **kwargs)
            for name, args, kwargs in self._ops
        ]
        self._ops.clear()
        return out

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.zsets = {}
        self.lists = {}
        self.sets = {}
        self.hashes = {}
        self.expires = {}

    def _sweep(self, key):
        deadline = self.expires.get(key)
        if deadline is not None and time.time() >= deadline:
            for d in (self.kv, self.zsets, self.lists, self.sets, self.hashes):
                d.pop(key, None)
            self.expires.pop(key, None)

    def _has(self, key):
        self._sweep(key)
        return any(
            key in d for d in (self.kv, self.zsets, self.lists, self.sets, self.hashes)
        )

    async def exists(self, *keys):
        return sum(1 for k in keys if self._has(k))

    async def set(
        self, key, value, nx=False, xx=False, px=None, ex=None, keepttl=False
    ):
        if nx and self._has(key):
            return None
        if xx and not self._has(key):
            return None
        self.kv[key] = str(value)
        ttl = (px / 1000.0) if px else (float(ex) if ex else None)
        if ttl is not None:
            self.expires[key] = time.time() + ttl
        elif not keepttl:
            self.expires.pop(key, None)
        return True

    async def get(self, key):
        if not self._has(key):
            return None
        return self.kv.get(key)

    async def delete(self, *keys):
        n = 0
        for k in keys:
            for d in (self.kv, self.zsets, self.lists, self.sets, self.hashes):
                if k in d:
                    del d[k]
                    n += 1
            self.expires.pop(k, None)
        return n

    async def expire(self, key, ttl):
        if not self._has(key):
            return False
        self.expires[key] = time.time() + float(ttl)
        return True

    async def ttl(self, key):
        if not self._has(key):
            return -2
        deadline = self.expires.get(key)
        return -1 if deadline is None else int(deadline - time.time())

    # sorted sets
    async def zadd(self, key, mapping, nx=False, xx=False, incr=False):
        z = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if nx and member in z:
                continue
            if xx and member not in z:
                continue
            if member not in z:
                added += 1
            z[member] = float(score)
        return added

    async def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        n = 0
        for m in members:
            if m in z:
                del z[m]
                n += 1
        return n

    async def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zcount(self, key, min_score, max_score):
        return len(self.zsets.get(key, {}))

    async def zrange(self, key, start, stop):
        ordered = sorted(self.zsets.get(key, {}), key=lambda m: self.zsets[key][m])
        stop = len(ordered) if stop == -1 else stop + 1
        return ordered[start:stop]

    async def zremrangebyscore(self, key, min_score, max_score):
        return 0

    def zscan_iter(self, match=None):
        async def gen():
            for member in list(
                self.zsets.get(match.replace("*", "") if match else "", {})
            ):
                yield member, 0

        return gen()

    # lists
    async def lrange(self, key, start, stop):
        values = self.lists.get(key, [])
        stop = len(values) if stop == -1 else stop + 1
        return values[start:stop]

    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(str(v) for v in values)
        return len(self.lists[key])

    async def lpush(self, key, *values):
        lst = self.lists.setdefault(key, [])
        for v in values:
            lst.insert(0, str(v))
        return len(lst)

    async def ltrim(self, key, start, stop):
        lst = self.lists.get(key, [])
        stop = len(lst) if stop == -1 else stop + 1
        self.lists[key] = lst[start:stop]
        return True

    # sets
    async def sadd(self, key, *values):
        s = self.sets.setdefault(key, set())
        before = len(s)
        s.update(str(v) for v in values)
        return len(s) - before

    async def srem(self, key, *values):
        s = self.sets.get(key, set())
        n = 0
        for v in values:
            if str(v) in s:
                s.discard(str(v))
                n += 1
        return n

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    # hashes
    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, mapping=None, **kwargs):
        h = self.hashes.setdefault(key, {})
        h.update(mapping or kwargs)
        return len(mapping or kwargs)

    async def hdel(self, key, *fields):
        h = self.hashes.get(key, {})
        n = 0
        for f in fields:
            if f in h:
                del h[f]
                n += 1
        return n

    async def eval(self, script, numkeys, *args):
        keys, argv = list(args[:numkeys]), list(args[numkeys:])
        if numkeys == 1 and self.kv.get(keys[0]) == (argv[0] if argv else None):
            del self.kv[keys[0]]
            return 1
        if numkeys == 3 and self.kv.get(keys[0]) == (argv[0] if argv else None):
            for k in keys:
                self.kv.pop(k, None)
            return 1
        return 0

    def pipeline(self, transaction=True):
        return _Pipeline(self)


class _Sent:
    """Заглушка отправленного сообщения: обработчики держат его в руках
    (status.edit_text / status.delete / .bot), а реального Bot API нет."""

    def __init__(self, bot, payload):
        self.bot = bot
        self.payload = payload
        self.text = getattr(payload, "text", None) or getattr(payload, "caption", None)
        self.chat_id = getattr(payload, "chat_id", None)

    async def edit_text(self, text, **kwargs):
        self.text = text
        return self

    async def edit_caption(self, caption=None, **kwargs):
        self.text = caption
        return self

    async def delete(self, *args, **kwargs):
        return True

    async def answer(self, text, **kwargs):
        return _Sent(self.bot, _Payload(text, self.chat_id))


class _Payload:
    def __init__(self, text, chat_id):
        self.text = text
        self.caption = None
        self.chat_id = chat_id


# --------------------------------------------------------------------------- #
BOT = Bot(
    token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH",
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
BOT._me = User(id=1, is_bot=True, first_name="Test", username="offline_test_bot")
BOT2 = Bot(
    token="7828301604:SECOND_TOKEN_FOR_OFFLINE_DISPATCH",
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
BOT2._me = User(id=2, is_bot=True, first_name="Second", username="offline_second_bot")

_uid = [700000]
_update_id = [900000]


def next_uid():
    _uid[0] += 1
    return _uid[0]


def next_update_id():
    _update_id[0] += 1
    return _update_id[0]


class AttackCase(unittest.IsolatedAsyncioTestCase):
    """Полный прод-пайплайн: роутеры + AccessMiddleware + AntiFlood + FSM.

    `flood_guard = True` оставляет настоящий check_flood включённым — нужно,
    чтобы измерять сам антиспам-гейт; по умолчанию он выключен, иначе
    собственные же тапы теста съедает антифлуд.
    """

    flood_guard = False

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "attack.db")
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in (
            "models.base",
            "repositories.chats",
            "repositories.users",
            "services.notifications",
            "services.registration",
            "services.growth",
            "services.payments",
            "services.profile",
            "services.matching",
            "services.settings",
            "services.reveal",
            "services.reports",
            "services.admin",
            "services.ads", "services.op",
        ):
            p = patch(module + ".async_session", self.factory)
            p.start()
            self.addCleanup(p.stop)
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)

        self.redis = FakeRedis()
        # Патчим ВСЕ пространства имён с top-level-привязкой get_redis: при
        # полном прогоне коллекция импортирует их ДО этого патча (яд --
        # tests/test_search_recovery.py -> handlers.search), и репорт-флоу
        # (report_details -> begin_search -> search_lock) достучался до
        # настоящего redis -> ConnectionError в ловушке ошибок.
        fake_redis = AsyncMock(return_value=self.redis)
        for target in (
            "services.chat_manager.get_redis",
            "handlers.search.get_redis",
            "handlers.chat.get_redis",
            "services.ads.get_redis",
            "utils.deduplication.get_redis",
            "utils.healthcheck.get_redis",
        ):
            __import__(target.rsplit(".", 1)[0])
            p = patch(target, fake_redis)
            p.start()
            self.addCleanup(p.stop)

        for router in load_routers():
            router._parent_router = None
        self.dp = Dispatcher()
        self.dp.include_routers(*load_routers())
        # Порядок обязан повторять bot.py после фикса NL-02: AntiFlood --
        # outer и ПЕРЕД Access, иначе Access возвращает до флуд-гейта и тест
        # меряет сломанную сборку, а не проверяет защиту.
        self.dp.message.outer_middleware(AntiFloodMiddleware())
        self.dp.callback_query.outer_middleware(AntiFloodMiddleware())
        self.dp.message.outer_middleware(AccessMiddleware())
        self.dp.callback_query.outer_middleware(AccessMiddleware())

        self.sent = []
        self.bot_errors = []
        self.request = AsyncMock(side_effect=self._record)
        self._req_patch = patch.object(BOT.session, "make_request", self.request)
        self._req_patch.start()
        self.addCleanup(self._req_patch.stop)
        self._req2_patch = patch.object(BOT2.session, "make_request", self.request)
        self._req2_patch.start()
        self.addCleanup(self._req2_patch.stop)

        for target in ("services.growth.StreakService.checkin",):
            p = patch(target, AsyncMock())
            p.start()
            self.addCleanup(p.stop)

        if not self.flood_guard:
            p = patch("utils.middleware.check_flood", AsyncMock(return_value=False))
            p.start()
            self.addCleanup(p.stop)

        self._install_error_probe()

    def _install_error_probe(self):
        """Ловим исключения, которые в проде уходят в @dp.errors()."""
        dp = self.dp

        @dp.errors()
        async def _probe(event, bot=None):
            self.bot_errors.append(event.exception)
            return True

    async def asyncTearDown(self):
        await self.dp.storage.close()
        for router in load_routers():
            router._parent_router = None
        await self.engine.dispose()
        self.temp.cleanup()

    def _record(self, *args, **kwargs):
        payload = args[1]
        # Bot defaults apply parse_mode=HTML at serialization time, which is
        # past the point where this mock sits; the sentinel below means exactly
        # "the default will be applied". A payload with parse_mode=None is the
        # deliberate plain-text send (relay edits) and is safe by construction.
        parse_mode = getattr(payload, "parse_mode", None)
        broken = telegram_would_reject_html(
            getattr(payload, "text", None)
        ) or telegram_would_reject_html(getattr(payload, "caption", None))
        if parse_mode is not None and broken:
            # Real API answer: 400 "Bad Request: can't parse entities".
            raise TelegramBadRequest(
                method=payload, message="Bad Request: can't parse entities"
            )
        self.sent.append(payload)
        return _Sent(BOT, payload)

    # --------------------------------------------------------------- helpers
    async def send(self, text, uid, bot=BOT, update_id=None, content=None, **kw):
        update_id = update_id or next_update_id()
        fields = dict(
            message_id=update_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=uid, type="private"),
            from_user=User(
                id=uid,
                is_bot=False,
                first_name=kw.pop("first_name", "Tester"),
                language_code=kw.pop("lang_code", "ru"),
            ),
        )
        if content is not None:
            fields.update(content)
        else:
            fields["text"] = text
        return await self.dp.feed_update(
            bot, Update(update_id=update_id, message=Message(**fields))
        )

    async def tap(self, data, uid, bot=BOT, update_id=None):
        update_id = update_id or next_update_id()
        return await self.dp.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=str(update_id),
                    from_user=User(id=uid, is_bot=False, first_name="Tester"),
                    chat_instance="offline",
                    data=data,
                    message=Message(
                        message_id=update_id,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=uid, type="private"),
                        from_user=User(
                            id=1,
                            is_bot=True,
                            first_name="Test",
                            username="offline_test_bot",
                        ),
                        text="stub",
                    ),
                ),
            ),
        )

    async def register(self, uid, bot=BOT):
        await self.send("/start", uid, bot=bot)
        await self.tap("lang:ru", uid, bot=bot)
        await self.send("Парень", uid, bot=bot)
        await self.send("25", uid, bot=bot)
        await self.send("Принимаю правила", uid, bot=bot)

    async def state_of(self, uid, bot=BOT):
        return await self.dp.fsm.get_context(
            bot=bot, chat_id=uid, user_id=uid
        ).get_state()

    async def data_of(self, uid, bot=BOT):
        return await self.dp.fsm.get_context(
            bot=bot, chat_id=uid, user_id=uid
        ).get_data()

    async def user_row(self, uid):
        async with self.factory() as s:
            return await s.scalar(select(UserModel).where(UserModel.telegram_id == uid))

    async def set_state(self, uid, state, bot=BOT, **data):
        ctx = self.dp.fsm.get_context(bot=bot, chat_id=uid, user_id=uid)
        await ctx.set_state(state)
        if data:
            await ctx.update_data(**data)

    def texts(self):
        return [p.text for p in self.sent if getattr(p, "text", None)]


class SmokeTests(AttackCase):
    async def test_harness_runs_a_full_registration(self):
        uid = next_uid()
        await self.register(uid)
        self.assertEqual(await self.state_of(uid), None)
        user = await self.user_row(uid)
        self.assertTrue(user.onboarding_completed)
        self.assertEqual(self.bot_errors, [])


# --------------------------------------------------------------------------- #
# 1. Некорректные переходы FSM
# --------------------------------------------------------------------------- #
class FsmTransitionTests(AttackCase):
    async def test_start_in_the_middle_of_a_profile_edit(self):
        """/start посреди редактирования: состояние должно развалиться, а не зависнуть."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_age", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:AGE")
        await self.send("/start", uid)
        self.assertEqual(
            await self.state_of(uid),
            None,
            f"/start left FSM state {await self.state_of(uid)}",
        )
        self.assertEqual(await self.data_of(uid), {})
        self.assertEqual(self.bot_errors, [])

    async def test_a_message_for_another_state_during_age_edit(self):
        """«Парень»/«/help»/мусорный текст во время ввода возраста: состояние
        должно пережить их — иначе живой редактор умирает на чужом слове."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_age", uid)
        for probe in ("Парень", "/help", "просто текст", "二十五"):
            await self.send(probe, uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        self.assertIsNotNone(
            await self.state_of(uid), "age edit died on a stray message"
        )

    async def test_menu_cancels_an_age_edit(self):
        """«Меню» — единственная кнопка, которая имеет право развалить редактор."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_age", uid)
        await self.send("Меню", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        self.assertEqual(await self.state_of(uid), None)

    async def test_stale_callback_from_another_state(self):
        """Кнопка «О себе» при открытом подтверждении удаления профиля."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:delete", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:DELETE_CONFIRM")
        await self.tap("profile:set_bio", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")
        self.assertEqual(self.bot_errors, [])

    async def test_delete_confirm_survives_a_stray_text_message(self):
        """DELETE_CONFIRM не имеет message-хендлера: куда девается обычный текст."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:delete", uid)
        before = len(self.sent)
        await self.send("просто текст", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        # состояние и сообщение-подтверждение должны пережить мусор
        self.assertEqual(await self.state_of(uid), "ProfileEdit:DELETE_CONFIRM")
        self.assertGreater(len(self.sent), before, "user got no answer at all")

    async def test_report_details_state_has_an_exit(self):
        uid = next_uid()
        await self.register(uid)
        await self.set_state(uid, "ReportFlow:DETAILS", report_session="no-such")
        await self.send("описание жалобы", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        self.assertEqual(await self.state_of(uid), None)
        self.assertEqual(await self.data_of(uid), {})

    async def test_report_details_without_a_session_in_storage(self):
        """report_session потерян → sid=None уходит в ReportService.submit."""
        uid = next_uid()
        await self.register(uid)
        await self.set_state(uid, "ReportFlow:DETAILS")
        await self.send("описание без сессии", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)


# --------------------------------------------------------------------------- #
# 2. Гонка состояний
# --------------------------------------------------------------------------- #
class FsmRaceTests(AttackCase):
    async def test_two_messages_in_the_same_state_at_once(self):
        """asyncio.gather: два сообщения в ProfileEdit.GENDER одновременно."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_gender", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:GENDER")
        await asyncio.gather(
            self.send("Парень", uid), self.send("Девушка", uid), return_exceptions=True
        )
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertIn(
            user.gender,
            {"male", "female"},
            f"gender left as {user.gender!r} after the race",
        )
        self.assertEqual(await self.state_of(uid), None)

    async def test_a_state_clearing_tap_racing_the_state_bound_handler(self):
        """«Меню» чистит состояние в AccessMiddleware, параллельный текст уже
        dispatched по закешированному raw_state в ProfileEdit.BIO."""
        for attempt in range(40):
            uid = next_uid()
            await self.register(uid)
            await self.tap("profile:edit", uid)
            await self.tap("profile:set_bio", uid)
            self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")
            results = await asyncio.gather(
                self.send("какой-то био текст", uid),
                self.send("Меню", uid),
                return_exceptions=True,
            )
            raised = [r for r in results if isinstance(r, BaseException)]
            if raised:
                self.fail(f"attempt {attempt}: {raised[0]!r}")
            if self.bot_errors:
                self.fail(f"attempt {attempt}: {self.bot_errors[0]!r}")
            self.assertEqual(await self.state_of(uid), None)

    async def test_double_report_details_submit(self):
        """FINDING: the duplicate check in services/reports.py:27 runs before
        the INSERT in its own statement, so two descriptions submitted at the
        same moment both pass it and the second dies on the UNIQUE index."""
        from models.report import Report

        uid, partner = next_uid(), next_uid()
        await self.register(uid)
        await self.register(partner)
        session = await self._seed_active_chat(uid, partner)
        await self.tap(f"v1:report:{session}:other", uid)
        await asyncio.gather(
            self.send("первая деталь", uid),
            self.send("вторвая деталь", uid),
            return_exceptions=True,
        )
        self.assertEqual(
            self.bot_errors,
            [],
            f"an unhandled IntegrityError reached the error trap: {self.bot_errors!r}",
        )
        async with self.factory() as s:
            reports = (await s.execute(select(Report))).scalars().all()
        self.assertEqual(len(reports), 1, f"double report: {len(reports)}")

    async def test_double_report_button_tap_races_the_same_check(self):
        """NL-03 through the reason buttons: no DETAILS state needed, so a
        double tap on the report keyboard is enough to hit the check-then-insert
        window in services/reports.py:27-29 — neither tap may crash."""
        from sqlalchemy.exc import IntegrityError

        reproduced = []
        for _ in range(5):
            self.bot_errors.clear()
            uid, partner = next_uid(), next_uid()
            await self.register(uid)
            await self.register(partner)
            session = await self._seed_active_chat(uid, partner)
            await asyncio.gather(
                self.tap(f"v1:report:{session}:insult", uid),
                self.tap(f"v1:report:{session}:spam", uid),
                return_exceptions=True,
            )
            reproduced.append(
                any(isinstance(e, IntegrityError) for e in self.bot_errors)
            )
        self.assertEqual(
            reproduced,
            [False] * 5,
            f"NL-03: UNIQUE violation in {sum(reproduced)}/5 races: {reproduced}",
        )

    async def test_a_second_report_after_the_first_is_refused_cleanly(self):
        """Control: outside the race window the duplicate is handled as text."""
        from models.report import Report

        uid, partner = next_uid(), next_uid()
        await self.register(uid)
        await self.register(partner)
        session = await self._seed_active_chat(uid, partner)
        await self.tap(f"v1:report:{session}:other", uid)
        await self.send("первая деталь", uid)
        await self.tap(f"v1:report:{session}:spam", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        self.assertTrue(any("уже отправлена" in t for t in self.texts()), self.texts())
        async with self.factory() as s:
            reports = (await s.execute(select(Report))).scalars().all()
        self.assertEqual(len(reports), 1)

    async def _seed_active_chat(self, a, b):
        from utils.time import utcnow

        session = "s" + str(a) + str(b) + "x"
        session = session[:32]
        async with self.factory() as s:
            s.add(
                ChatModel(
                    session_id=session,
                    status="active",
                    user1_id=a,
                    user2_id=b,
                    started_at=utcnow(),
                    last_activity_at=utcnow(),
                )
            )
            await s.flush()
            s.add(ActiveParticipant(user_id=a, session_id=session))
            s.add(ActiveParticipant(user_id=b, session_id=session))
            await s.commit()
        return session


# --------------------------------------------------------------------------- #
# 3. Полезная нагрузка
# --------------------------------------------------------------------------- #
class PayloadAttackTests(AttackCase):
    async def _bio_editor(self, uid):
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")

    async def test_5000_character_bio_never_reaches_the_database(self):
        uid = next_uid()
        await self._bio_editor(uid)
        await self.send("а" * 5000, uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertIsNone(user.bio, "5000 chars got past the 150 limit")
        self.assertEqual(
            await self.state_of(uid),
            "ProfileEdit:BIO",
            "editor died instead of asking again",
        )
        self.assertTrue(any("Слишком длинно" in t for t in self.texts()))

    async def test_hundred_kilobyte_report_description(self):
        """Telegram caps text at 4096, so this is the belt behind that brace."""
        uid, partner = next_uid(), next_uid()
        await self.register(uid)
        await self.register(partner)
        session = await self._seed_active_chat(uid, partner)
        await self.set_state(uid, "ReportFlow:DETAILS", report_session=session)
        await self.send("х" * 100_000, uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        self.assertEqual(await self.state_of(uid), "ReportFlow:DETAILS")
        self.assertTrue(
            any("1000" in t for t in self.texts()), "no range hint for a 100KB body"
        )

    async def test_one_thousand_emoji_bio(self):
        """1000 code points is one grapheme cluster per emoji -- still over 150."""
        uid = next_uid()
        await self._bio_editor(uid)
        await self.send("\U0001f600" * 1000, uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertIsNone(user.bio)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")

    async def test_control_and_zero_width_characters_are_stripped_from_the_bio(self):
        """Bidi overrides and zero-width joiners survive today and are rendered
        verbatim in the card the partner reads."""
        uid = next_uid()
        await self._bio_editor(uid)
        payload = "a\u200bb\u202ec\u200dd\u2066e\u2069f"
        await self.send(payload, uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertEqual(
            user.bio, "abcdef", "bidi/zero-width controls reached the column"
        )

    async def test_a_nul_byte_is_refused_before_the_write(self):
        """No sanitising layer exists between the tap and the column."""
        uid = next_uid()
        await self._bio_editor(uid)
        await self.send("a\x00b", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertNotIn("\x00", user.bio or "", "NUL was accepted into the column")

    async def test_nul_byte_is_rejected_cleanly_on_a_postgres_driver(self):
        """asyncpg refuses to bind a string containing NUL (ValueError), so the
        bio that SQLite swallows aborts the whole transaction in prod: the
        handler dies, the state freezes and every retry crashes the same way."""
        from sqlalchemy import event

        def reject_nul(conn, cursor, statement, parameters, context, executemany):
            for value in parameters or ():
                if isinstance(value, str) and "\x00" in value:
                    raise ValueError(
                        "A string literal cannot contain NUL (0x00) characters."
                    )

        event.listen(self.engine.sync_engine, "before_cursor_execute", reject_nul)
        self.addCleanup(
            event.remove, self.engine.sync_engine, "before_cursor_execute", reject_nul
        )
        uid = next_uid()
        await self._bio_editor(uid)
        await self.send("a\x00b", uid)
        self.assertEqual(
            self.bot_errors,
            [],
            f"a NUL in the bio crashed the handler: {self.bot_errors!r}",
        )
        self.assertTrue(
            any("Проверь" in t or "Check" in t for t in self.texts()),
            "the user got no feedback for the rejected bio",
        )
        self.assertEqual(
            await self.state_of(uid),
            "ProfileEdit:BIO",
            "state froze on the failed save",
        )

    async def test_an_unpaired_surrogate_kills_the_handler_before_the_write(self):
        """No layer validates the string, so the failure surfaces at the driver
        itself as UnicodeEncodeError and the transaction dies mid-handler.
        Telegram only ever hands over valid UTF-8, so this documents the
        missing validation rather than a hole a client can reach."""
        uid = next_uid()
        await self._bio_editor(uid)
        await self.send("broken\ud800text", uid)
        self.assertTrue(
            any(isinstance(e, UnicodeEncodeError) for e in self.bot_errors),
            f"expected a driver-level encode failure, got {self.bot_errors!r}",
        )
        user = await self.user_row(uid)
        self.assertIsNone(user.bio, "a half-written row would be worse")
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")

    async def _seed_active_chat(self, a, b):
        from utils.time import utcnow

        session = ("s" + str(a) + str(b) + "x")[:32]
        async with self.factory() as s:
            s.add(
                ChatModel(
                    session_id=session,
                    status="active",
                    user1_id=a,
                    user2_id=b,
                    started_at=utcnow(),
                    last_activity_at=utcnow(),
                )
            )
            await s.flush()
            s.add(ActiveParticipant(user_id=a, session_id=session))
            s.add(ActiveParticipant(user_id=b, session_id=session))
            await s.commit()
        return session


# --------------------------------------------------------------------------- #
# 4. Юникод
# --------------------------------------------------------------------------- #
class UnicodeAttackTests(AttackCase):
    async def test_nbsp_age_is_accepted_in_onboarding_and_in_profile_edit(self):
        """NL-09: handlers/start.py:176 normalises NBSP, handlers/profile.py:278
        does not — the same "30 " passes onboarding and dies in the editor."""
        uid = next_uid()
        await self.send("/start", uid)
        await self.tap("lang:ru", uid)
        await self.send("Парень", uid)
        await self.send("25\u00a0", uid)
        user = await self.user_row(uid)
        self.assertEqual(user.age, 25, "onboarding must survive the stray NBSP")
        await self.send("Принимаю правила", uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_age", uid)
        await self.send("30\u00a0", uid)
        user = await self.user_row(uid)
        self.assertEqual(
            user.age,
            30,
            "NL-09: the editor refused an NBSP age the onboarding accepted",
        )
        self.assertIsNone(
            await self.state_of(uid),
            "the editor parks the user on a rejected NBSP age",
        )

    async def test_trailing_space_age_is_accepted_in_profile_edit(self):
        """NL-09: "30 " fails text.isdigit() at handlers/profile.py:278 while
        handlers/start.py:176 strips the same input."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_age", uid)
        await self.send("30 ", uid)
        user = await self.user_row(uid)
        self.assertEqual(user.age, 30, "NL-09: a trailing space killed the age")
        self.assertIsNone(await self.state_of(uid))

    async def test_fullwidth_and_indic_digits_are_rejected_with_a_hint(self):
        for payload in ("\uff12\uff15", "\u0663\u0665", "\xb2"):
            uid = next_uid()
            await self.send("/start", uid)
            await self.tap("lang:ru", uid)
            await self.send("Парень", uid)
            before = len(self.texts())
            await self.send(payload, uid)
            user = await self.user_row(uid)
            self.assertIsNone(user.age, f"{payload!r} was accepted as an age")
            self.assertTrue(
                any("18" in t for t in self.texts()[before:]),
                f"no age hint for {payload!r}: tap looks dead",
            )

    async def test_rtl_override_never_reaches_the_card_the_partner_sees(self):
        """NL-11: only the Bidi_Control class is filtered, and U+202E is
        category Cf — it mirrors the order of every word after it on the
        partner's screen."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        payload = "\u202eadmin\u202c: 100%"  # RLO ... PDF
        await self.send(payload, uid)
        user = await self.user_row(uid)
        self.assertNotIn(
            "\u202e", user.bio or "", "NL-11: the bidi override was stored verbatim"
        )
        before = len(self.texts())
        await self.send("/profile", uid)
        card = [t for t in self.texts()[before:] if "О себе" in t]
        self.assertTrue(card, "the card did not render")
        self.assertNotIn(
            "\u202e", card[0], "NL-11: a bidi control reaches the partner's screen"
        )

    async def test_zero_width_bio_is_stripped_before_it_is_saved(self):
        """NL-11: ZWSP is category Cf too — a bio of pure invisible characters
        saves as an empty line in the card."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        await self.send("\u200b\u200b\u200b", uid)
        user = await self.user_row(uid)
        self.assertNotIn(
            "\u200b",
            user.bio or "",
            "NL-11: invisible characters were stored as the bio",
        )


# --------------------------------------------------------------------------- #
# 5. SQL-инъекции
# --------------------------------------------------------------------------- #
class SqlInjectionTests(AttackCase):
    SCAN_DIRS = (
        "handlers",
        "services",
        "repositories",
        "utils",
        "workers",
        "models",
        "web",
    )

    async def test_payload_in_the_bio_stays_a_literal(self):
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        await self.send("'; DROP TABLE users; --", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertEqual(user.bio, "'; DROP TABLE users; --")
        async with self.factory() as s:
            count = len((await s.execute(select(UserModel))).scalars().all())
        self.assertGreater(count, 0, "the users table is gone")

    async def test_payload_in_report_details_stays_a_literal(self):
        uid, partner = next_uid(), next_uid()
        await self.register(uid)
        await self.register(partner)
        session = await self._seed_active_chat(uid, partner)
        await self.set_state(uid, "ReportFlow:DETAILS", report_session=session)
        await self.send("x'; DELETE FROM reports; --", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        from models.report import Report

        async with self.factory() as s:
            reports = (await s.execute(select(Report))).scalars().all()
        self.assertEqual(len(reports), 1)
        self.assertIn("DELETE FROM reports", reports[0].message_content)

    async def test_payload_in_the_report_session_callback_never_matches_a_chat(self):
        uid, partner = next_uid(), next_uid()
        await self.register(uid)
        await self.register(partner)
        await self._seed_active_chat(uid, partner)
        await self.tap("v1:report:' OR '1'='1:insult", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        from models.report import Report

        async with self.factory() as s:
            reports = (await s.execute(select(Report))).scalars().all()
        self.assertEqual(reports, [], "a forged session id produced a report")

    async def test_payload_in_the_age_field_never_reaches_sql(self):
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_age", uid)
        await self.send("1 OR 1=1", uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertEqual(user.age, 25)

    async def test_static_scan_finds_no_interpolated_sql(self):
        import re

        text_call = re.compile(r"\btext\(\s*(f[\"']|[\"'][^\"']*\{|.*%s|.*\+.*[\"'])")
        execute_call = re.compile(
            r"\.execute\(\s*(f[\"']|.*\.format\(|.*%\s*\(|.*\+\s*[\"'])"
        )
        findings = []
        for name in self.SCAN_DIRS:
            base = ROOT / name
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if text_call.search(line) or execute_call.search(line):
                        findings.append(
                            f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
                        )
        self.assertEqual(findings, [], "interpolated SQL found")

    async def _seed_active_chat(self, a, b):
        from utils.time import utcnow

        session = ("i" + str(a) + str(b) + "x")[:32]
        async with self.factory() as s:
            s.add(
                ChatModel(
                    session_id=session,
                    status="active",
                    user1_id=a,
                    user2_id=b,
                    started_at=utcnow(),
                    last_activity_at=utcnow(),
                )
            )
            await s.flush()
            s.add(ActiveParticipant(user_id=a, session_id=session))
            s.add(ActiveParticipant(user_id=b, session_id=session))
            await s.commit()
        return session


# --------------------------------------------------------------------------- #
# 6. Возраст
# --------------------------------------------------------------------------- #
class AgeValidationTests(AttackCase):
    REJECTED = ("-5", "0", "17", "100", "999", "abc", "", "-1", "17,5", "1e2")

    async def test_config_bounds_are_18_and_99(self):
        import config

        self.assertEqual((config.MIN_AGE, config.MAX_AGE), (18, 99))

    async def test_every_out_of_range_age_is_refused_with_a_hint(self):
        for payload in self.REJECTED:
            uid = next_uid()
            await self.send("/start", uid)
            await self.tap("lang:ru", uid)
            await self.send("Парень", uid)
            before = len(self.texts())
            await self.send(payload, uid)
            user = await self.user_row(uid)
            self.assertIsNone(user.age, f"{payload!r} set an age")
            self.assertTrue(
                any("18" in t for t in self.texts()[before:]),
                f"silent tap for {payload!r}",
            )
            self.assertEqual(self.bot_errors, [], self.bot_errors)

    async def test_the_boundaries_are_accepted(self):
        for payload, expected in (("18", 18), ("99", 99)):
            uid = next_uid()
            await self.send("/start", uid)
            await self.tap("lang:ru", uid)
            await self.send("Парень", uid)
            await self.send(payload, uid)
            user = await self.user_row(uid)
            self.assertEqual(user.age, expected, f"{payload!r} was refused")

    async def test_a_4096_digit_age_is_refused_not_crashed(self):
        """Telegram caps text at 4096 and Python's int() limit is 4300, so the
        digit-limit ValueError cannot be reached through the Bot API."""
        uid = next_uid()
        await self.send("/start", uid)
        await self.tap("lang:ru", uid)
        await self.send("Парень", uid)
        await self.send("9" * 4096, uid)
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        user = await self.user_row(uid)
        self.assertIsNone(user.age)

    async def test_profile_edit_refuses_the_same_range(self):
        uid = next_uid()
        await self.register(uid)
        for payload in self.REJECTED:
            await self.tap("profile:edit", uid)
            await self.tap("profile:set_age", uid)
            before = len(self.texts())
            await self.send(payload, uid)
            self.assertEqual(
                await self.state_of(uid),
                "ProfileEdit:AGE",
                f"{payload!r} closed the editor without saving",
            )
            user = await self.user_row(uid)
            self.assertEqual(user.age, 25, f"{payload!r} overwrote a valid age")
            self.assertTrue(any("Проверь данные" in t for t in self.texts()[before:]))
        self.assertEqual(self.bot_errors, [], self.bot_errors)


# --------------------------------------------------------------------------- #
# 7. HTML-инъекция в карточку профиля
# --------------------------------------------------------------------------- #
class HtmlInjectionTests(AttackCase):
    async def _set_bio(self, uid, payload):
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        await self.send(payload, uid)

    async def _profile_cards(self, uid, taps=1):
        before = len(self.texts())
        for _ in range(taps):
            await self.send("/profile", uid)
        return [t for t in self.texts()[before:] if "О себе" in t or "About" in t]

    async def test_an_unescaped_less_than_breaks_the_card_forever(self):
        uid = next_uid()
        await self._set_bio(uid, "I <3 cats")
        user = await self.user_row(uid)
        self.assertEqual(user.bio, "I <3 cats")
        cards = await self._profile_cards(uid, taps=2)
        self.assertEqual(len(cards), 2, "every card must survive a bio containing «<»")
        self.assertEqual(self.bot_errors, [], self.bot_errors)

    async def test_an_unbalanced_bold_tag_breaks_the_card(self):
        uid = next_uid()
        await self._set_bio(uid, "<b>oops")
        cards = await self._profile_cards(uid)
        self.assertEqual(len(cards), 1, self.bot_errors)
        self.assertEqual(self.bot_errors, [], self.bot_errors)

    async def test_well_formed_markup_still_renders(self):
        """Proves the fix is escaping, not stripping: valid markup survives."""
        uid = next_uid()
        await self._set_bio(uid, "<b>bold</b> and <i>italic</i>")
        cards = await self._profile_cards(uid)
        self.assertEqual(len(cards), 1, self.bot_errors)
        self.assertEqual(self.bot_errors, [])

    async def test_escaped_markup_is_safe(self):
        uid = next_uid()
        await self._set_bio(uid, "&lt;3 cats")
        cards = await self._profile_cards(uid)
        self.assertEqual(len(cards), 1, self.bot_errors)
        # Введённое экранировано ровно один раз: сырой HTML содержит &amp;lt;,
        # и Telegram отдаёт пользователю ровно то, что он ввёл -- &lt;3 cats.
        # До фикса сырая сущность &lt; прочитывалась Telegram как тег.
        self.assertIn("&amp;lt;3 cats", cards[0])

    async def test_the_save_step_itself_answers_before_the_card_fails(self):
        uid = next_uid()
        await self._set_bio(uid, "<a href")
        self.assertTrue(
            any("Сохранено" in t for t in self.texts()), "user saw nothing at all"
        )


# --------------------------------------------------------------------------- #
# 8. Хранилище FSM: два бота и отказ Redis
# --------------------------------------------------------------------------- #
class CrossBotStorageTests(AttackCase):
    async def test_state_written_through_one_bot_is_invisible_to_the_other(self):
        uid = next_uid()
        await self.register(uid)
        ctx2 = self.dp.fsm.get_context(bot=BOT2, chat_id=uid, user_id=uid)
        self.assertIsNone(await ctx2.get_state(), "bot2 sees bot1's FSM state")
        await self.set_state(uid, "ProfileEdit:BIO")
        self.assertIsNone(await ctx2.get_state())
        ctx2 = self.dp.fsm.get_context(bot=BOT2, chat_id=uid, user_id=uid)
        await ctx2.set_state("ProfileEdit:PHOTO")
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")
        self.assertEqual(await ctx2.get_state(), "ProfileEdit:PHOTO")

    async def test_the_flood_bucket_is_scoped_per_bot(self):
        """NL-10: services/chat_manager.py:69 keys the bucket rate:message:{user_id}
        with no bot id — everywhere else (dedupe, FSM StorageKey) carries bot.id."""
        from services.chat_manager import check_flood as real_flood

        uid = next_uid()
        await self.register(uid)
        spy = AsyncMock(wraps=real_flood)
        with patch("utils.middleware.check_flood", spy):
            await self.send("Мой профиль", uid)
            before = len(self.texts())
            await self.send("Мой профиль", uid, bot=BOT2)
        self.assertEqual(spy.await_count, 2)
        self.assertFalse(
            any(
                "Подожди" in t or "Wait" in t or "Зачекай" in t
                for t in self.texts()[before:]
            ),
            "NL-10: bot2 inherited bot1's rate-limit key — one key, no bot id",
        )


class FsmStorageOutageTests(AttackCase):
    async def test_midflow_input_during_a_storage_outage_is_not_swallowed_silently(
        self,
    ):
        """NL-08: utils/storage.py:8-13 lets a failed read fall back to
        state=None; the message then falls through to handlers/chat.py:180,
        which returns on NOT_CHATTING with no answer and no exception — in prod
        @dp.errors() never fires, so the user gets pure silence."""
        from redis.exceptions import RedisError

        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")
        # Как в проде: под защитой только get_state (RedisError -> None),
        # остальные операции падают сырым RedisError.
        storage = self.dp.fsm.storage
        with (
            patch.object(storage, "get_state", AsyncMock(return_value=None)),
            patch.object(
                storage, "get_data", AsyncMock(side_effect=RedisError("down"))
            ),
            patch.object(
                storage, "set_state", AsyncMock(side_effect=RedisError("down"))
            ),
            patch.object(
                storage, "set_data", AsyncMock(side_effect=RedisError("down"))
            ),
            patch.object(
                storage, "update_data", AsyncMock(side_effect=RedisError("down"))
            ),
        ):
            before = len(self.sent)
            await self.send("мой новый био", uid)
        self.assertEqual(
            self.bot_errors,
            [],
            "the outage stayed exception-free — prod's error trap never runs",
        )
        self.assertGreater(
            len(self.sent),
            before,
            "NL-08: mid-flow input vanished during a storage outage — no reply at all",
        )
        self.assertEqual(
            await self.state_of(uid),
            "ProfileEdit:BIO",
            "the state must survive the outage untouched",
        )

    async def test_a_broken_fsm_write_surfaces_as_a_retriable_error(self):
        from redis.exceptions import RedisError

        uid = next_uid()
        await self.register(uid)
        with patch.object(
            self.dp.fsm.storage, "set_state", AsyncMock(side_effect=RedisError("down"))
        ):
            await self.tap("profile:set_age", uid)
        self.assertTrue(
            any(isinstance(e, RedisError) for e in self.bot_errors),
            f"expected the storage failure to reach the error trap, got {self.bot_errors!r}",
        )

    async def test_save_field_survives_a_state_read_that_returns_none(self):
        """NL-07: handlers/profile.py:248 re-reads the state instead of using
        the raw_state the dispatcher already resolved; the second read can
        return None (menu landing, storage hiccup) and the handler dies on
        None.split(), losing the input."""
        storage = self.dp.fsm.storage
        original = storage.get_state
        reads = {"n": 0}

        async def racy_get_state(key):
            reads["n"] += 1
            if reads["n"] == 1:
                return await original(key)
            return None

        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:edit", uid)
        await self.tap("profile:set_bio", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:BIO")
        with patch.object(storage, "get_state", racy_get_state):
            await asyncio.gather(
                self.send("био, которое не сохранится", uid), return_exceptions=True
            )
        self.assertFalse(
            any(isinstance(e, AttributeError) for e in self.bot_errors),
            f"NL-07: save_field died on a None state read: {self.bot_errors!r}",
        )
        user = await self.user_row(uid)
        self.assertEqual(
            user.bio,
            "био, которое не сохранится",
            "NL-07: the input was dropped because the handler re-read the state",
        )


# --------------------------------------------------------------------------- #
# 9. Антиспам-гейт
# --------------------------------------------------------------------------- #
class FloodGateTests(AttackCase):
    async def test_the_flood_gate_is_consulted_for_a_stranger(self):
        """Защита: антифлуд спрашивается ДО регистрации, а не пропускает
        незарегистрированного сквозь весь пайплайн онбординга."""
        from services.chat_manager import check_flood as real_flood

        uid = next_uid()
        spy = AsyncMock(wraps=real_flood)
        with patch("utils.middleware.check_flood", spy):
            for _ in range(20):
                await self.send("привет", uid)
        self.assertEqual(
            spy.await_count, 20, "the flood gate was never consulted for a stranger"
        )
        # Каждый тап получает ответ: либо онбординг, либо тост slow_down --
        # молчаливых пропусков нет.
        self.assertGreaterEqual(len(self.texts()), 20, "some of the 20 taps were dropped")
        self.assertEqual(self.bot_errors, [], self.bot_errors)

    async def test_the_gate_fails_open_while_redis_is_down(self):
        """@redis_fallback(False) turns a RedisError into "not flooding", so a
        Redis outage switches the throttle off entirely instead of throttling."""
        from redis.exceptions import RedisError

        from services.chat_manager import check_flood as real_flood

        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.chat_manager.get_redis",
            AsyncMock(side_effect=RedisError("down")),
        ):
            with patch("utils.middleware.check_flood", real_flood):
                for _ in range(20):
                    await self.send("Мой профиль", uid)
        throttled = [
            t for t in self.texts() if "Подожди" in t or "Wait" in t or "Зачекай" in t
        ]
        self.assertTrue(
            throttled,
            "the gate failed open while Redis was down instead of throttling",
        )
        self.assertGreaterEqual(len(self.texts()), 20, "some taps were dropped")
        self.assertEqual(self.bot_errors, [], self.bot_errors)

    async def test_the_gate_does_work_once_the_user_is_registered(self):
        from services.chat_manager import check_flood as real_flood

        uid = next_uid()
        await self.register(uid)
        spy = AsyncMock(wraps=real_flood)
        with patch("utils.middleware.check_flood", spy):
            for _ in range(5):
                await self.send("Мой профиль", uid)
        self.assertGreaterEqual(spy.await_count, 1)
        self.assertTrue(
            any("Подожди" in t or "Wait" in t or "Зачекай" in t for t in self.texts())
        )


# --------------------------------------------------------------------------- #
# 10. Зависшие состояния: полный проход
# --------------------------------------------------------------------------- #
class StuckStateSweepTests(AttackCase):
    STATES = [
        "Onboarding:LANGUAGE",
        "Onboarding:WELCOME",
        "Onboarding:GENDER",
        "Onboarding:AGE",
        "Onboarding:RULES",
        "ProfileEdit:GENDER",
        "ProfileEdit:AGE",
        "ProfileEdit:BIO",
        "ProfileEdit:PHOTO",
        "ProfileEdit:DELETE_CONFIRM",
        "ReportFlow:DETAILS",
    ]
    ESCAPES = ["/start", "/cancel", "Меню", "Мой профиль", "/help", "просто текст"]

    async def test_every_state_answers_every_escape(self):
        """Onboarding states only ever belong to an account that has not
        finished registering, so they are swept with a fresh stranger; the
        editing and report states belong to a registered user."""
        registered = next_uid()
        await self.register(registered)
        silent, stuck = [], []
        for state in self.STATES:
            uid = (
                registered
                if state.startswith(("ProfileEdit", "ReportFlow"))
                else next_uid()
            )
            for escape in self.ESCAPES:
                await self.set_state(uid, state, age_page=0, report_session="no-such")
                before = len(self.sent)
                await self.send(escape, uid)
                if len(self.sent) == before:
                    silent.append(f"{state} + {escape!r}")
                    if await self.state_of(uid) == state:
                        stuck.append(f"{state} + {escape!r}")
        self.assertEqual(self.bot_errors, [], self.bot_errors)
        self.assertEqual(silent, [], f"no answer at all: {silent}")
        self.assertEqual(stuck, [], f"state never moved: {stuck}")

    async def test_delete_confirmation_is_only_reachable_through_its_buttons(self):
        """Second reproducer of the same hole, from the other side: the state
        survives any stray word, and only the two inline buttons can move it."""
        uid = next_uid()
        await self.register(uid)
        await self.tap("profile:delete", uid)
        self.assertEqual(await self.state_of(uid), "ProfileEdit:DELETE_CONFIRM")
        before = len(self.sent)
        await self.send("да нет всё-таки", uid)
        # Фолбэк отвечает повтором вопроса (NL-04), но состояние не двигает:
        # выйти можно только по inline-кнопкам.
        self.assertGreater(len(self.sent), before, "тишина вместо повтора вопроса")
        self.assertEqual(
            await self.state_of(uid),
            "ProfileEdit:DELETE_CONFIRM",
            "state parked: only the inline buttons can leave it",
        )


# --------------------------------------------------------------------------- #
# 11. Дедупликация апдейтов
# --------------------------------------------------------------------------- #
class DedupTests(AttackCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from utils.deduplication import UpdateDeduplication

        self.dp.update.outer_middleware(UpdateDeduplication())

    async def test_a_replayed_update_id_is_handled_once(self):
        uid = next_uid()
        update_id = next_update_id()
        await self.send("/start", uid, update_id=update_id)
        first = len(self.sent)
        self.assertGreater(first, 0)
        await self.send("/start", uid, update_id=update_id)
        self.assertEqual(len(self.sent), first, "the replay ran a second time")

    async def test_a_concurrent_replay_is_handled_once(self):
        uid = next_uid()
        update_id = next_update_id()
        from aiogram.types import Chat, Message, Update, User
        from datetime import datetime, timezone

        build = lambda: Update(
            update_id=update_id,
            message=Message(
                message_id=update_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=uid, type="private"),
                from_user=User(
                    id=uid, is_bot=False, first_name="Tester", language_code="ru"
                ),
                text="/start",
            ),
        )
        await asyncio.gather(*(self.dp.feed_update(BOT, build()) for _ in range(3)))
        # One /start answers with exactly one screen; three replays would answer
        # three times (or write the same referral/onboarding rows three over).
        self.assertEqual(
            len(self.sent), 1, f"replays produced {len(self.sent)} screens"
        )
        self.assertEqual(self.bot_errors, [], self.bot_errors)

    async def test_a_failed_handler_stays_retryable(self):
        from services.onboarding import OnboardingService

        uid = next_uid()
        update_id = next_update_id()
        with patch.object(
            OnboardingService, "start", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await self.send("/start", uid, update_id=update_id)
        self.assertTrue(any(isinstance(e, RuntimeError) for e in self.bot_errors))
        before = len(self.sent)
        await self.send("/start", uid, update_id=update_id)
        self.assertGreater(len(self.sent), before, "a failed update was marked done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
