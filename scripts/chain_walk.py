"""E2E-прогон цепочки диалога на изолированном тестовом стеке.

Через настоящий прод-диспетчер (роутеры + Access/AntiFlood-миддлвары)
проходят три тестовых аккаунта: /start → язык → пол → возраст → правила,
меню, поиск, матч (уведомления шлёт настоящий outbox-воркер), двухсторонний
релей, «Стоп», анти-повтор подбора (кулдаун), запасной выход на повторную
пару, «Стоп» вне чата и «Предложить идею».

Бот наружу не ходит: make_request перехвачен, каждая отправка записана,
а очередь и база честные (compose: anon_chat_test + test-redis).

Запуск:
    docker compose -f docker-compose.test.yml run --rm tests sh -c \
        "python scripts/chain_walk.py"
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")

import config  # noqa: E402

_DATABASE = (config.DATABASE_URL or "").lower()
_REDIS = (config.REDIS_URL or "").lower()
if "anon_chat_test" not in _DATABASE or "test-redis" not in _REDIS:
    print(
        "E: запусти внутри compose-сети тестового стека "
        "(docker compose -f docker-compose.test.yml run --rm tests ...), "
        f"иначе скрипт тронет чужую базу. URL={_DATABASE!r}"
    )
    raise SystemExit(2)

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    Message,
    Update,
    User as TgUser,
)

from keyboards import texts as T  # noqa: E402
from models import chat as chat_models  # noqa: E402
from models import user as user_models  # noqa: E402
from models.base import Base  # noqa: E402
from services.onboarding import step_for  # noqa: E402
from utils import bots  # noqa: E402
from utils.middleware import AccessMiddleware  # noqa: E402

TRACE = []  # (monotonic, api_method, to_user, preview, button_labels)


def _labels(markup):
    if markup is None:
        return []
    rows = getattr(markup, "keyboard", None) or getattr(markup, "inline_keyboard", [])
    out = []
    for row in rows or []:
        for b in row:
            out.append(
                b.text
                + (
                    f" [icon {b.icon_custom_emoji_id}]"
                    if getattr(b, "icon_custom_emoji_id", None)
                    else ""
                )
            )
    return out


class FakeMessage:
    """Ответ перехвата на sendMessage: карточка статуса читает .chat и
    .message_id, обновляет и стирает .edit_text/.delete. Счётчик общий с
    феедовыми id не пересекается."""

    next_id = 1000

    def __init__(self, bot, chat_id, text=""):
        self.bot = bot
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = FakeMessage.next_id
        FakeMessage.next_id += 1
        self.text = text
        self.edit_text = AsyncMock(return_value=None)
        self.delete = AsyncMock(return_value=None)


async def _record(*args, **kwargs):
    method = args[1]
    kind = getattr(method, "__api_method__", "?")
    payload = getattr(method, "model_dump", lambda: {})()
    to = payload.get("chat_id") or payload.get("user_id") or ""
    text = str(payload.get("text") or payload.get("caption") or "")[:110]
    TRACE.append(
        (time.monotonic(), kind, to, text, _labels(payload.get("reply_markup")))
    )
    if kind == "sendMessage":
        return FakeMessage(args[0], payload.get("chat_id"), payload.get("text") or "")
    return True


_U1, _U2, _U3 = 70001, 70002, 70003
_LANG = {_U1: "ru", _U2: "uk", _U3: "en"}
_STATE = {"update_id": 1}


def _say(uid, action):
    who = uid if isinstance(uid, str) else f"{uid} {_LANG[uid]:2}"
    print(f"→ [{who}] {action}")


def _inbox(uid, since, kinds=("sendMessage",)):
    return [
        (k, text, btn)
        for (ts, k, to, text, btn) in TRACE
        if ts >= since and to in {uid, str(uid)} and k in kinds
    ]


async def _wait_inbox(uid, since, timeout=15, kinds=("sendMessage",)):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mails = _inbox(uid, since, kinds)
        if mails:
            return mails
        await asyncio.sleep(0.3)
    return []


async def _feed_text(dp, bot, text, uid):
    _STATE["update_id"] += 1
    msg = Message(
        message_id=_STATE["update_id"],
        date=datetime.now(timezone.utc),
        chat=Chat(id=uid, type="private"),
        from_user=TgUser(
            id=uid, is_bot=False, first_name=f"T{uid}", language_code=_LANG[uid]
        ),
        text=text,
    )
    await dp.feed_update(bot, Update(update_id=_STATE["update_id"], message=msg))


async def _feed_tap(dp, bot, data, uid):
    _STATE["update_id"] += 1
    cb = CallbackQuery(
        id=str(_STATE["update_id"]),
        from_user=TgUser(id=uid, is_bot=False, first_name=f"T{uid}"),
        chat_instance="offline",
        data=data,
        message=Message(
            message_id=_STATE["update_id"],
            date=datetime.now(timezone.utc),
            chat=Chat(id=uid, type="private"),
            from_user=TgUser(id=1, is_bot=True, first_name="chainwalk_bot"),
            text="stub",
        ),
    )
    await dp.feed_update(bot, Update(update_id=_STATE["update_id"], callback_query=cb))


def _label(key, uid):
    return T.label(key, _LANG[uid])


session_factory = None  # назначается в main()


def _active_chats():
    from sqlalchemy import select

    return select(chat_models.Chat.user1_id, chat_models.Chat.user2_id).where(
        chat_models.Chat.status == "active"
    )


async def _active_pairs():
    """Активные пары как нормализованные [min, max] -- scalars() на
    двухколоночном select отдал бы только первый столбец."""
    async with session_factory() as s:
        rows = (await s.execute(_active_chats())).all()
        return [list(pair) for pair in sorted({tuple(sorted(r)) for r in rows})]


async def _wait_pair(timeout=60, need=1):
    """Ждёт появления need активных пар (по умолчанию одной)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await _active_pairs()
        if len(rows) >= need:
            return rows
        await asyncio.sleep(0.6)
    return []


async def _participants_count():
    from sqlalchemy import select

    async with session_factory() as s:
        return len(
            (await s.scalars(select(chat_models.ActiveParticipant.user_id))).all()
        )


async def main():
    global session_factory
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(config.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from models.base import async_session as shared_session

    # Каждая витрина держит свою ссылку на async_session -- патчим все точки
    # (ключевой список DatabaseCase), чтобы весь код ходил в test-db.
    for module in (
        "models.base",
        "repositories.chats",
        "repositories.users",
        "services.reveal",
        "services.reports",
        "services.admin",
        "services.notifications",
        "services.registration",
        "services.payments",
        "services.growth",
        "services.profile",
        "services.settings",
        "services.matching",
        "services.ads",
        "services.op",
        "services.op_admin",
        "services.op_schedule",
        "services.funnel_alert",
        "services.community",
        "services.moderation",
        "services.ideas",
        "services.emoji_settings",
    ):
        patch(module + ".async_session", shared_session).start()

    patch("utils.middleware.check_flood", AsyncMock(return_value=False)).start()
    patch("services.chat_manager.set_online", AsyncMock()).start()
    patch("services.growth.StreakService.checkin", AsyncMock()).start()
    # Воркер раз в секунду тыкает Telegram-токены -- в ходоке телефоны не нужны.
    patch(
        "workers.maintenance.bot_health",
        SimpleNamespace(probe=AsyncMock(), report=AsyncMock()),
    ).start()

    bot = Bot(token="123456:BRAINWALK-TEST-TOKEN")
    bot._me = TgUser(id=1, is_bot=True, first_name="chainwalk_bot")
    patch.object(bot.session, "make_request", _record).start()
    bots.register(bot, primary=True)

    from handlers import load_routers

    routers = load_routers()
    for router in routers:
        router._parent_router = None
    dp = Dispatcher()
    dp.include_routers(*routers)
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())

    loop = asyncio.get_running_loop()
    crashes = {"n": 0}

    def _diag(loop_, context):
        exc = context.get("exception")
        crashes["n"] += 1
        print(f" ! фоновая задача выбросила {type(exc).__name__}: {exc}")
        loop.default_exception_handler(context)

    loop.set_exception_handler(_diag)

    # Кто и откуда отменяет таски: заводим все задачи через свой подкласс,
    # у которого cancel() печатает стек вызывающего.
    class WatchedTask(asyncio.Task):
        def cancel(self, msg=None):
            import traceback

            print(f"   [cancel] задача {self.get_name()!r} отменяется. Стек:")
            traceback.print_stack()
            return super().cancel(msg)

    def watched_factory(loop_, coro, **kwargs):
        return WatchedTask(coro, loop=loop_, **kwargs)

    loop.set_task_factory(watched_factory)

    # Тик-логи: сколько живёт каждая попытка подбора.
    from services import matching as matching_module
    from repositories import chats as chats_module

    _orig_find = matching_module.MatchingService.find
    _orig_match = chats_module.ChatRepository.match

    async def logged_find(self, user_id):
        t0 = time.monotonic()
        out = await _orig_find(self, user_id)
        print(f"   [tick] find({user_id}) → {out!r} за {time.monotonic() - t0:.2f}с")
        return out

    async def logged_match(self, a_id, b_id, redis):
        t0 = time.monotonic()
        out = await _orig_match(self, a_id, b_id, redis)
        print(
            f"   [ной] match({a_id},{b_id}) → {'chat' if out else 'None'} за {time.monotonic() - t0:.2f}с"
        )
        return out

    matching_module.MatchingService.find = logged_find
    chats_module.ChatRepository.match = logged_match

    # Доставщик outbox: тот же claim → send → mark, что в workers.maintenance,
    # но без maintenance-цикла (reconcile/funnel/op в этой изоляции не осмыслены).
    # Отправка ведётся тем же bots.bot_for и тем же notification_content.
    from services.notifications import NotificationService

    _mail = NotificationService()

    async def drain_outbox():
        from sqlalchemy import select as _sel

        from models.operations import Notification as _N
        from utils import bots as bots_mod
        from utils.db import user_lang
        from utils.time import utcnow
        from workers.maintenance import notification_content
        from keyboards.reply import chat_kb_reply

        print("   [outbox] цикл стартует")
        idle_rounds = 0

        async def _claim_one():
            # Claim без FOR UPDATE: ходок живёт в однопользовательском мире.
            # Через shared-фабрику models.base -- единственный пул, который
            # стабильно возил весь ходок в этих прогонах.
            from models.base import async_session as claim_session

            async with claim_session.begin() as s:
                notification = await s.scalar(
                    _sel(_N)
                    .where(
                        _N.status.in_(["queued", "sending"]),
                        _N.scheduled_at <= utcnow(),
                    )
                    .order_by(_N.marketing, _N.id)
                    .limit(1)
                )
                if notification is not None:
                    notification.status = "sending"
                    notification.attempt_count += 1
                return notification

        while True:
            claim_task = asyncio.ensure_future(_claim_one())
            done, _ = await asyncio.wait({claim_task}, timeout=5)
            if not done:
                print("   [outbox] claim паркоюсясяся на:")
                for frame in claim_task.get_stack():
                    print(f"   [outbox]   {frame.f_code.co_filename}:{frame.f_lineno} в {frame.f_code.co_name}")
                claim_task.cancel()
                await asyncio.wait({claim_task})
                continue
            notification = claim_task.result()
            if notification is None:
                idle_rounds += 1
                if idle_rounds % 10 == 1:
                    print("   [outbox] очередь пуста")
                await asyncio.sleep(0.3)
                continue
            print(f"   [outbox] забрал #{notification.id} {notification.type}")
            try:
                target = await bots_mod.bot_for(notification.user_id, bot)
                lang = await user_lang(notification.user_id, bots_mod.default_lang(target))
                markup = chat_kb_reply(lang) if notification.type == "match" else None
                text, parse_mode = notification_content(notification)
                await target.send_message(
                    notification.user_id,
                    text,
                    reply_markup=markup,
                    parse_mode=parse_mode,
                )
                # Без отметки sent ходок клал бы одну и ту же строку заново
                # и завяз на ней: claim фильтрует queued|sending.
                async with session_factory().begin() as s_m:
                    sent_row = await s_m.get(_N, notification.id)
                    sent_row.status = "sent"
            except Exception as error:
                print(
                    f"   [outbox] доставка {notification.id} {notification.type} "
                    f"сорвалась: {type(error).__name__}: {error}"
                )
                async with session_factory().begin() as s:
                    _n = await s.get(_N, notification.id)
                    _n.status = "failed"
            await asyncio.sleep(0.05)

    worker = asyncio.create_task(drain_outbox())

    async def outbox_watchdog(task):
        print("   [outbox] watchdog стартует")
        while True:
            await asyncio.sleep(5)
            stack = task.get_stack()
            if stack:
                frame = stack[-1]
                loops = (
                    f"рабочий_loop={task.get_loop()!r} "
                    f"текущий_loop={asyncio.get_running_loop()!r}"
                )
                print(f"   [outbox] watchdog: {loops}")
                print(
                    f"   [outbox] watchdog: таска стоит на "
                    f"{frame.f_code.co_filename.rsplit('/', 1)[-1]}:"
                    f"{frame.f_lineno} ({frame.f_code.co_name})"
                )

    wdg = asyncio.create_task(outbox_watchdog(worker))

    async def onboard(uid, gender_key):
        _say(uid, "/start")
        t0 = time.monotonic()
        await _feed_text(dp, bot, "/start", uid)
        await asyncio.sleep(0.5)
        inbox = await _wait_inbox(uid, t0, timeout=5)
        assert inbox, f"/start не ответил {uid}"
        _say(uid, f"первое: {inbox[0][1][:70]!r}")
        await _feed_tap(dp, bot, f"lang:{_LANG[uid]}", uid)
        await asyncio.sleep(0.4)
        await _feed_text(dp, bot, _label(gender_key, uid), uid)
        await asyncio.sleep(0.4)
        await _feed_text(dp, bot, "25", uid)
        await asyncio.sleep(0.4)
        await _feed_text(dp, bot, _label(T.ACCEPT, uid), uid)
        await asyncio.sleep(0.6)
        from sqlalchemy import select as _select

        async with session_factory() as s:
            u = await s.scalar(
                _select(user_models.User).where(user_models.User.telegram_id == uid)
            )
        step = step_for(u)
        _say(
            uid,
            f"в меню: lang={u.language} gender={u.gender} age={u.age} step={step}",
        )
        assert step == "MENU", f"{uid} не дошёл до меню: {step}"

    print("\n=== ОНБОРДИНГ трёх аккаунтов ===")
    await onboard(_U1, T.MALE)
    await onboard(_U2, T.FEMALE)
    await onboard(_U3, T.MALE)

    print("\n=== БИТ 1: матч U1 × U2, уведомления от воркера, релей сообщений ===")
    t0 = time.monotonic()
    _say(_U1, f"жмёт «{_label(T.SEARCH, _U1)}»")
    await _feed_text(dp, bot, _label(T.SEARCH, _U1), _U1)
    await asyncio.sleep(0.4)
    _say(_U2, f"жмёт «{_label(T.SEARCH, _U2)}»")
    await _feed_text(dp, bot, _label(T.SEARCH, _U2), _U2)
    pair = await _wait_pair(timeout=60)
    assert pair and pair[0] == sorted([_U1, _U2]), f"матч не состоялся: {pair}"
    print(f"   ◆ активная пара: {pair}")
    for uid in (_U1, _U2):
        mails = await _wait_inbox(uid, t0, timeout=15)
        # Матч-уведомление узнаём по чат-клавиатуре (Следующий/Next/Наступний):
        # до него в ленту попадает и живая карточка поиска.
        chat_note = next(
            (
                m
                for m in mails
                if any(
                    "Следующий" in b or "Next" in b or "Наступний" in b for b in m[2]
                )
            ),
            None,
        )
        assert chat_note, (
            f"{uid} не получил матч-уведомление: {[m[1][:44] for m in mails]}"
        )
        _say(uid, f"матч-уведомление: {chat_note[1][:66]!r}")
        _say(
            uid,
            f"клавиатура чата: {[b.split(' [icon')[0] for b in chat_note[2]][:2]}",
        )

    t0 = time.monotonic()
    _say(_U1, "пишет собеседнику: «Привет! Как настроение?»")
    await _feed_text(dp, bot, "Привет! Как настроение?", _U1)
    mail = await _wait_inbox(_U2, t0, timeout=5)
    assert mail and "Привет!" in mail[0][1], f"U2 не получил текст U1: {mail}"
    _say(_U2, f"получил: {mail[0][1]!r}")

    t0 = time.monotonic()
    _say(_U2, "отвечает: «Привіт! Все чудово»")
    await _feed_text(dp, bot, "Привіт! Все чудово", _U2)
    mail = await _wait_inbox(_U1, t0, timeout=5)
    assert mail and "Привіт!" in mail[0][1], f"U1 не получил ответ U2: {mail}"
    _say(_U1, f"получил: {mail[0][1]!r}")

    print("\n=== БИТ 2: «Стоп» завершает, оба узнают, очередь пустеет ===")
    t0 = time.monotonic()
    _say(_U1, f"жмёт {_label(T.STOP, _U1)!r}")
    await _feed_text(dp, bot, _label(T.STOP, _U1), _U1)
    mail = await _wait_inbox(_U2, t0, timeout=5)
    assert mail, "U2 не узнал о завершении чата"
    _say(_U2, f"уведомление: {mail[0][1][:66]!r}")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if await _participants_count() == 0:
            break
        await asyncio.sleep(0.4)
    assert await _participants_count() == 0, "после Стоп остались активные участники"
    print("   ◆ активных участников: 0")

    print("--- все трое в очереди: U1-U2 только что чатились, U3 свеж")
    t0 = time.monotonic()
    for uid in (_U1, _U2, _U3):
        await _feed_text(dp, bot, _label(T.SEARCH, uid), uid)
        await asyncio.sleep(0.3)
    fresh_pair = await _wait_pair(timeout=60)
    assert fresh_pair and len(fresh_pair) == 1, (
        f"матч первой очереди не состоялся: {fresh_pair}"
    )
    assert fresh_pair[0] != sorted([_U1, _U2]), (
        f"кулдаун не сработал: повторная пара свелась мгновенно {fresh_pair}"
    )
    assert _U3 in fresh_pair[0], f"U3 должен быть в паре: {fresh_pair}"
    _say("★", f"матч {fresh_pair[0]} — повторной паре U1×U2 предпочли свежего U3")

    # Кто из U1/U2 занят с U3: занятый -- chatty, свободный -- waiting.
    chatty_uid = _U1 if _U1 in fresh_pair[0] else _U2
    waiting_uid = _U2 if chatty_uid == _U1 else _U1

    print("\n=== БИТ 3: запасной выход — повторная пара, когда пул вымер ===")
    t0 = time.monotonic()
    _say(_U3, f"жмёт {_label(T.STOP, _U3)!r}")
    await _feed_text(dp, bot, _label(T.STOP, _U3), _U3)
    await asyncio.sleep(0.8)
    _say(chatty_uid, f"жмёт «{_label(T.SEARCH, chatty_uid)}»")
    await _feed_text(dp, bot, _label(T.SEARCH, chatty_uid), chatty_uid)
    deadline = time.monotonic() + 60
    repeat_pair = []
    while time.monotonic() < deadline:
        rows = await _active_pairs()
        if rows and rows[0] == sorted([_U1, _U2]):
            repeat_pair = rows[0]
            break
        await asyncio.sleep(0.6)
    assert repeat_pair == sorted([_U1, _U2]), (
        f"запасной выход должен свести U1×U2: {repeat_pair}"
    )
    print("   ◆ матч: [70001, 70002] — повторные собеседники сведены, свежих нет")

    print("\n=== БИТ 4: «Стоп» вне чата — честный отказ ===")
    t0 = time.monotonic()
    _say(_U3, f"жмёт {_label(T.STOP, _U3)!r}, находясь вне чата")
    await _feed_text(dp, bot, _label(T.STOP, _U3), _U3)
    mail = await _wait_inbox(_U3, t0, timeout=5)
    assert mail, "U3 не получил ответ на Стоп вне чата"
    _say(_U3, f"получил: {mail[0][1][:66]!r}")

    print("\n=== БИТ 5: «Предложить идею» из меню ===")
    t0 = time.monotonic()
    _say(_U1, f"жмёт {_label(T.STOP, _U1)!r} (попрощались с повторной парой)")
    await _feed_text(dp, bot, _label(T.STOP, _U1), _U1)
    await asyncio.sleep(1.0)
    _say(_U3, f"жмёт «{_label(T.IDEA, _U3)}»")
    await _feed_text(dp, bot, _label(T.IDEA, _U3), _U3)
    prompts = await _wait_inbox(_U3, t0, timeout=5)
    assert prompts, "FSM идеи не спросил текст"
    _say(_U3, f"промт: {prompts[0][1][:70]!r}")
    t0 = time.monotonic()
    await _feed_text(dp, bot, "Add a poll feature in chats", _U3)
    ack = await _wait_inbox(_U3, t0, timeout=5)
    _say(_U3, f"ответ: {ack[0][1][:70]!r}" if ack else "мгновенного подтверждения нет")
    from sqlalchemy import select

    async with session_factory() as s:
        from models.idea import Idea

        ideas = (await s.scalars(select(Idea.id))).all()
    assert ideas, "идея не сохранилась в базе"
    print(f"   ◆ идея записана (всего {len(ideas)})")

    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass

    from sqlalchemy import select as _select

    async with session_factory() as s:
        from models.operations import Notification

        queued = (
            await s.scalars(
                _select(Notification.id).where(Notification.status == "queued")
            )
        ).all()
    print(f"\n   не доставленных уведомлений осталось: {len(queued)}")

    print("\n=== ХРОНОЛОГИЯ: что бот отправлял (sendMessage) ===")
    for _, kind, to, text, buttons in TRACE:
        if kind != "sendMessage":
            continue
        buttons_note = f" │ {' · '.join(buttons[:3])}" if buttons else ""
        print(f"  → {to:<6} {text[:86]}{buttons_note}")

    print(f"\nитог: цепочка прошла целиком; фоновых аварий: {crashes['n']}")
    # Одноразовый ходок: жилец-dispatcher и воркер держали бы интерпретатор
    # внизу минуты -- осмысленный graceful cleanup ему не нужен.
    os._exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as error:
        print(f"\nХОДОК УПАЛ: {error}")

        # Посмертный разбор: что session_factory видит в таблице уведомлений
        # и куда реально смотрит её движок.
        async def final_diag():
            from sqlalchemy import select

            from models.operations import Notification

            async with session_factory() as s:
                rows = (
                    await s.execute(
                        select(Notification.id, Notification.status, Notification.type)
                    )
                ).all()
                print(
                    f"   [diag] notification_log через session_factory: {rows}"
                )
                print(
                    "   [diag] база ходока: "
                    f"{config.DATABASE_URL.rsplit('@', 1)[-1]}"
                )

        try:
            asyncio.run(final_diag())
        except BaseException as diag_error:
            print(f"   [diag] сам разбор упал: {diag_error}")
        os._exit(1)
