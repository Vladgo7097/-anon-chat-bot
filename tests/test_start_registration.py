"""Учёт запусков: чек-лист приёмки из ТЗ, пункт за пунктом.

База -- SQLite-фабрика из DatabaseCase; окружение и патчи те же, что у
остальных сервисных тестов. Каждый тест проверяет наблюдаемое поведение,
а не устройство: строки в таблицах, состояния очереди, границы суток.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

from test_chat_duration import DatabaseCase

import config
from models.operations import Notification
from models.start import ProcessedUpdate, StartEvent
from models.user import User
from services.notifications import NotificationService
from services.registration import register_start, start_totals
from utils.time import kyiv_day_bounds, utcnow

OWNER = 99
BOT = 111


class StartRegistrationTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.OWNER_ID", OWNER)
        p.start()
        self.addCleanup(p.stop)

    async def user(self, telegram_id):
        async with self.factory() as s:
            return await s.scalar(select(User).where(User.telegram_id == telegram_id))

    async def notices(self):
        async with self.factory() as s:
            return (
                await s.scalars(
                    select(Notification).where(Notification.type == "owner_first_start")
                )
            ).all()

    # 1. Первый уникальный /start: строка users + событие + задача владельцу.
    async def test_first_start_creates_user_event_and_one_owner_card(self):
        result = await register_start(
            5001, BOT, 10, username="newbie", first_name="Иван"
        )
        self.assertEqual(result.code, "FIRST")
        self.assertEqual((await self.user(5001)).start_count, 1)
        async with self.factory() as s:
            events = (await s.scalars(select(StartEvent))).all()
            self.assertEqual(
                [(e.event_type, e.update_id, e.bot_id) for e in events],
                [("first_start", 10, BOT)],
            )
            notice = await s.scalar(
                select(Notification).where(Notification.type == "owner_first_start")
            )
            self.assertEqual(notice.user_id, OWNER)
            self.assertEqual(notice.bot_id, BOT)
            for chunk in (
                "НОВЫЙ ПОЛЬЗОВАТЕЛЬ",
                "Иван",
                "@newbie",
                "(Киев)",
                "Статистика после регистрации",
                "Всего пользователей: 4",
                "Всего запусков: 1",
                "Запусков сегодня: 1",
            ):
                self.assertIn(chunk, notice.text)
            # Плейн-текст: имя и username -- пользовательские данные, разметки
            # в тексте нет вовсе, экранировать нечего.
            self.assertNotIn("<", notice.text)
        # Задача поднимается тем же воркером, что и остальные уведомления.
        claimed = await NotificationService().next()
        self.assertEqual(claimed.type, "owner_first_start")

    # 2. Повторный /start: счётчик и событие есть, второго уведомления нет.
    async def test_repeat_start_counts_without_a_second_card(self):
        await register_start(5002, BOT, 11)
        result = await register_start(5002, BOT, 12)
        self.assertEqual(result.code, "REPEAT")
        self.assertEqual((await self.user(5002)).start_count, 2)
        async with self.factory() as s:
            types = (
                await s.scalars(select(StartEvent.event_type).order_by(StartEvent.id))
            ).all()
            self.assertEqual(list(types), ["first_start", "repeat_start"])
        self.assertEqual(len(await self.notices()), 1)

    # 3. Повторная доставка того же update_id: ни счётчиков, ни событий.
    async def test_duplicate_update_id_changes_nothing(self):
        await register_start(5003, BOT, 13)
        for _ in range(2):
            self.assertEqual((await register_start(5003, BOT, 13)).code, "DUPLICATE")
        self.assertEqual((await self.user(5003)).start_count, 1)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id))), 1)
            self.assertEqual(
                await s.scalar(
                    select(func.count(ProcessedUpdate.update_id)).where(
                        ProcessedUpdate.bot_id == BOT
                    )
                ),
                1,
            )
        self.assertEqual(len(await self.notices()), 1)

    # 4. Параллельные запуски: одна строка users, два события, одно уведомление.
    async def test_concurrent_starts_do_not_split_into_two_users(self):
        results = await asyncio.gather(
            register_start(5004, BOT, 14), register_start(5004, BOT, 15)
        )
        self.assertEqual(sorted(r.code for r in results), ["FIRST", "REPEAT"])
        async with self.factory() as s:
            users = (
                await s.scalars(select(User).where(User.telegram_id == 5004))
            ).all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].start_count, 2)
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id))), 2)
        self.assertEqual(len(await self.notices()), 1)

    # 5. Сбой транзакции: ни пользователя, ни события, ни задачи -- и повтор
    #    после сбоя проходит с чистого листа.
    async def test_a_failed_transaction_leaves_nothing_behind(self):
        with patch(
            "services.registration.enqueue", side_effect=RuntimeError("telegram down")
        ):
            with self.assertRaises(RuntimeError):
                await register_start(5005, BOT, 16, first_name="Пётр")
        async with self.factory() as s:
            self.assertIsNone(
                await s.scalar(select(User).where(User.telegram_id == 5005))
            )
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id))), 0)
            self.assertEqual(
                await s.scalar(select(func.count(ProcessedUpdate.update_id))), 0
            )
            self.assertEqual(await s.scalar(select(func.count(Notification.id))), 0)
        result = await register_start(5005, BOT, 16, first_name="Пётр")
        self.assertEqual(result.code, "FIRST")
        self.assertEqual((await self.user(5005)).start_count, 1)

    # 6. Сбой Telegram API: задача остаётся в очереди и уходит в ретрай,
    #    событие запуска никуда не девается.
    async def test_a_network_failure_keeps_the_task_for_retry(self):
        await register_start(5006, BOT, 17)
        service = NotificationService()
        notice = await service.next()
        self.assertEqual(notice.attempt_count, 1)
        await service.finish(
            notice.id, "queued", "network", min(300, 2**notice.attempt_count)
        )
        async with self.factory() as s:
            row = await s.get(Notification, notice.id)
            self.assertEqual(row.status, "queued")
            self.assertEqual(row.last_error, "network")
            self.assertGreater(row.scheduled_at, utcnow())
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id))), 1)

    # 7. Сбой после отправки: отметка sent идемпотентна, повторный /start
    #    второго уведомления не открывает. Дубликат при падении между
    #    send_message и отметкой возможен -- он остаётся в логе (dedupe_key).
    async def test_a_sent_card_is_not_delivered_twice(self):
        await register_start(5007, BOT, 18)
        service = NotificationService()
        notice = await service.next()
        await service.finish(notice.id, "sent")
        await service.finish(notice.id, "sent")  # повторная отметка -- no-op
        async with self.factory() as s:
            self.assertEqual((await s.get(Notification, notice.id)).status, "sent")
        self.assertIsNone(await service.next())
        await register_start(5007, BOT, 19)
        self.assertEqual(len(await self.notices()), 1)

    # 8. Лиза истекла (воркер умер посреди отправки): строка снова доступна.
    async def test_an_expired_lease_returns_the_row_to_the_worker(self):
        await register_start(5008, BOT, 20)
        service = NotificationService()
        notice = await service.next()
        async with self.factory.begin() as s:
            row = await s.get(Notification, notice.id)
            self.assertEqual(row.status, "sending")
            self.assertGreater(row.scheduled_at, utcnow())  # лиза держится
            row.scheduled_at = utcnow() - timedelta(seconds=1)  # лиза истекла
        again = await service.next()
        self.assertEqual(again.id, notice.id)
        self.assertEqual(again.attempt_count, 2)

    # 9. Владелец заблокировал бота: ошибка фиксируется, события целы.
    async def test_owner_blocking_the_bot_is_recorded_without_losing_events(self):
        await register_start(5009, BOT, 21)
        service = NotificationService()
        notice = await service.next()
        await service.finish(notice.id, "failed", "forbidden")
        async with self.factory() as s:
            self.assertEqual((await s.get(Notification, notice.id)).status, "failed")
            self.assertEqual(await s.scalar(select(func.count(StartEvent.id))), 1)
        self.assertEqual((await self.user(5009)).start_count, 1)

    # 10. Посторонний не проходит админ-гард.
    async def test_a_stranger_cannot_reach_the_admin_panel(self):
        from handlers.admin import AdminOnly

        handler, answers = AsyncMock(), []
        event = SimpleNamespace(
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(side_effect=lambda text, **kw: answers.append(text)),
        )
        with patch("config.ADMIN_IDS", {OWNER}):
            await AdminOnly()(handler, event, {})
        handler.assert_not_awaited()
        self.assertTrue(answers)
        self.assertIn("Нет доступа", answers[0])
        allowed = SimpleNamespace(
            from_user=SimpleNamespace(id=OWNER),
            answer=AsyncMock(side_effect=lambda text, **kw: answers.append(text)),
        )
        with patch("config.ADMIN_IDS", {OWNER}):
            await AdminOnly()(handler, allowed, {})
        handler.assert_awaited_once()

    # 11. Границы суток -- по календарю Киева, включая ночь перевода часов.
    async def test_day_boundaries_follow_the_kyiv_calendar(self):
        start, end = kyiv_day_bounds(
            datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(start, datetime(2026, 10, 18, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 10, 19, 21, 0, tzinfo=timezone.utc))
        # Ночь перевода часов: календарные сутки длятся 25 часов.
        start, end = kyiv_day_bounds(datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(end - start, timedelta(hours=25))

    async def test_daily_totals_split_by_kyiv_midnight(self):
        async with self.factory.begin() as s:
            s.add(
                User(
                    telegram_id=6101,
                    anon_id="#d1",
                    created_at=datetime(2026, 1, 14, 21, 59, tzinfo=timezone.utc),
                )
            )
            s.add(
                User(
                    telegram_id=6102,
                    anon_id="#d2",
                    created_at=datetime(2026, 1, 14, 22, 1, tzinfo=timezone.utc),
                )
            )
        async with self.factory() as s:
            totals = await start_totals(
                s, now=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
            )
        # Январь -- EET (+2): 22:00 UTC = полночь по Киеву. 6101 -- 14-е, 6102 -- 15-е.
        self.assertEqual(totals["new_today"], 1)

    # «Запусков» стоят первые нажатия людей: повторный /start и чужой
    # repeat_start цифру не двигают, иначе запусков больше, чем людей.
    async def test_totals_count_first_presses_only(self):
        async with self.factory.begin() as s:
            s.add(
                StartEvent(
                    telegram_user_id=5303,
                    bot_id=999,
                    update_id=43,
                    event_type="repeat_start",
                )
            )
        await register_start(5301, BOT, 40)
        await register_start(5301, BOT, 41)  # повторный заход того же человека
        await register_start(5302, BOT, 42)
        async with self.factory() as s:
            totals = await start_totals(s)
        self.assertEqual(totals["total_starts"], 2)
        self.assertEqual(totals["starts_today"], 2)

    # 12. После рестарта события и задачи на месте, очередь работает.
    async def test_after_a_restart_events_and_tasks_remain(self):
        await register_start(5010, BOT, 22)
        claimed = await NotificationService().next()
        self.assertIsNotNone(claimed)
        self.assertEqual((await self.user(5010)).start_count, 1)
        async with self.factory() as s:
            self.assertIsNotNone(
                await s.scalar(
                    select(StartEvent).where(StartEvent.telegram_user_id == 5010)
                )
            )

    # Бэкфил миграции: у старых пользователей start_count = 1, поэтому их
    # следующий /start -- повторный, а не «новый пользователь».
    async def test_a_user_with_backfilled_history_is_not_announced_as_new(self):
        async with self.factory.begin() as s:
            s.add(User(telegram_id=5200, anon_id="#old", start_count=1))
        result = await register_start(5200, BOT, 30)
        self.assertEqual(result.code, "REPEAT")
        self.assertEqual(await self.notices(), [])
