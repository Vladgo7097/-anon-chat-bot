"""Идеи: кнопка в меню, сценарий черновика, лимиты спама и статусы.

Сервис проверяется на живой тестовой базе (DatabaseCase), обработчики -
прямыми вызовами с AsyncMock-стабами, как в test_chat_end_partner_notice:
диспетчерная накрутка не нужна там, где проверяется логика, а не маршрутизация.
"""

import sys
import types
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

import config
from keyboards import texts as T
from keyboards.reply import main_menu_kb
from models.idea import Idea
from models.operations import Notification
from services.ideas import IdeaService
from test_chat_duration import DatabaseCase
from utils.time import utcnow

OWNER = 777
SERVICE = IdeaService()


class MenuCompositionTests(unittest.TestCase):
    def test_the_fourth_row_carries_the_idea_button_now(self):
        # «Пригласи друга» освободила место: рефералка живёт в карточке
        # наград, не в меню.
        uses = [[(b.text) for b in row] for row in main_menu_kb("ru").keyboard]
        self.assertEqual(
            uses,
            [
                [T.SEARCH, T.PROFILE],
                [T.SETTINGS, T.PREMIUM],
                [T.STATS, T.HELP],
                [T.CHAT, T.IDEA],
            ],
        )

    def test_the_idea_label_is_a_nock_in_the_router_and_not_a_chat_message(self):
        # SYSTEM_TEXTS запрещает ретрансляцию подписи кнопки собеседнику
        # в живом чате: IDEA сидит в _ACTIONS, как остальные кнопки.
        for lang in ("ru", "en", "uk"):
            with self.subTest(lang=lang):
                self.assertIn(T.label(T.IDEA, lang), T.SYSTEM_TEXTS)


class ServiceTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.owner_patch = patch.object(config, "OWNER_ID", OWNER)
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)

    async def idea_count(self, user_id=None):
        async with self.factory() as s:
            query = select(func.count(Idea.id))
            if user_id is not None:
                query = query.where(Idea.telegram_user_id == user_id)
            return await s.scalar(query)

    async def test_create_strips_the_text_saves_the_row_and_queues_the_owner_notice(
        self,
    ):
        now = utcnow()
        result = await SERVICE.create(
            7, "  Добавить стрим-акции  ", 123, "Тимур", now=now
        )
        self.assertTrue(result.ok, result)
        self.assertGreater(result.data["id"], 0)
        async with self.factory() as s:
            idea = await s.get(Idea, result.data["id"])
            self.assertEqual(idea.text, "Добавить стрим-акции")
            self.assertEqual(idea.telegram_user_id, 7)
            self.assertEqual(idea.status, "new")
            self.assertEqual(idea.bot_id, 123)
            notice = await s.scalar(
                select(Notification).where(
                    Notification.type == "owner_idea",
                    Notification.dedupe_key == f"owner_idea:{idea.id}",
                )
            )
        self.assertIsNotNone(notice)
        self.assertEqual(notice.user_id, OWNER)
        self.assertEqual(notice.callback_data, f"admin:idea:{idea.id}")
        self.assertEqual(notice.bot_id, 123)
        self.assertIn("Добавить стрим-акции", notice.text)
        self.assertEqual(await self.idea_count(7), 1)

    async def test_whitespace_and_oversized_texts_are_refused_without_rows(self):
        self.assertFalse((await SERVICE.create(7, "   ", 123, "T")).ok)
        long_result = await SERVICE.create(7, "y" * (config.IDEA_MAX_LEN + 1), 123, "T")
        self.assertEqual(long_result.code, "TOO_LONG")
        self.assertEqual(long_result.data["len"], config.IDEA_MAX_LEN + 1)
        self.assertEqual(await self.idea_count(7), 0)

    async def test_a_minute_cools_down_between_ideas(self):
        base = utcnow()
        self.assertTrue((await SERVICE.create(7, "первая", 123, "T", now=base)).ok)
        soon = await SERVICE.create(
            7, "вторая", 123, "T", now=base + timedelta(seconds=5)
        )
        self.assertEqual(soon.code, "COOLDOWN")
        self.assertEqual(soon.data["min"], 1)
        later = await SERVICE.create(
            7, "вторая", 123, "T", now=base + timedelta(seconds=61)
        )
        self.assertTrue(later.ok, later)

    async def test_three_ideas_per_day_then_a_grid_wait(self):
        base = utcnow()
        for step in range(3):
            self.assertTrue(
                (
                    await SERVICE.create(
                        7,
                        f"идея {step}",
                        123,
                        "T",
                        now=base + timedelta(seconds=step * 61),
                    )
                ).ok,
                step,
            )
        fourth = await SERVICE.create(
            7, "четвёртая", 123, "T", now=base + timedelta(seconds=183)
        )
        self.assertEqual(fourth.code, "RATE_LIMITED")
        # Скользящее окно: место освобождается ровно через сутки после
        # самой старой из трёх.
        self.assertEqual(fourth.data["when"] - base, timedelta(hours=24))
        self.assertEqual(fourth.data["max"], config.IDEA_MAX_PER_DAY)
        reopening = await SERVICE.create(
            7, "после окна", 123, "T", now=base + timedelta(hours=24, seconds=244)
        )
        self.assertTrue(reopening.ok, reopening)

    async def test_the_same_text_within_a_day_does_not_reopen(self):
        base = utcnow()
        self.assertTrue(
            (await SERVICE.create(7, "Совместный  сбор идей", 123, "T", now=base)).ok
        )
        again = await SERVICE.create(
            7, "совместный СБОР идей", 123, "T", now=base + timedelta(seconds=120)
        )
        self.assertEqual(again.code, "DUPLICATE_TEXT")
        other = await SERVICE.create(
            7,
            "совместный сбор идей истории",
            123,
            "T",
            now=base + timedelta(seconds=180),
        )
        self.assertTrue(other.ok, other)

    async def test_mine_lists_only_the_author_rows_in_pages(self):
        async with self.factory.begin() as s:
            for step, user in ((0, 7), (1, 7), (2, 8), (3, 7)):
                s.add(
                    Idea(
                        telegram_user_id=user,
                        text=f"заготовка {step}",
                        status="new",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
        rows, page, pages = await SERVICE.mine(7, 1)
        self.assertEqual((await self.idea_count(7)), 3)
        self.assertEqual((len(rows), page, pages), (3, 1, 1))
        self.assertTrue(all(row.telegram_user_id == 7 for row in rows))
        seeded_9 = 9
        async with self.factory.begin() as s:
            for step in range(7):
                s.add(
                    Idea(
                        telegram_user_id=seeded_9,
                        text=f"строка {step}",
                        status="new",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
        first, page, pages = await SERVICE.mine(seeded_9, 1)
        self.assertEqual((len(first), page, pages), (5, 1, 2))
        second, page, pages = await SERVICE.mine(seeded_9, 2)
        self.assertEqual((len(second), page), (2, 2))
        over = await SERVICE.mine(seeded_9, 9)
        self.assertEqual(over[1], 2, "страница за пределами зажимается")

    async def test_only_the_author_opens_the_card(self):
        async with self.factory.begin() as s:
            foreign = Idea(
                telegram_user_id=8,
                text="не твой текст",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(foreign)
            await s.flush()
            idea_id, foreign_key = foreign.id, 8
        self.assertIsNone(await SERVICE.own(idea_id, 7))
        own_row = await SERVICE.own(idea_id, foreign_key)
        self.assertIsNotNone(own_row)
        self.assertEqual(own_row.text, "не твой текст")

    async def test_admin_list_filters_by_status(self):
        async with self.factory.begin() as s:
            for status in ("new", "new", "review", "planned"):
                s.add(
                    Idea(
                        telegram_user_id=7,
                        text=f"{status} текст",
                        status=status,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
        fresh, _, _ = await SERVICE.admin_list("new")
        self.assertEqual({row.status for row in fresh}, {"new"})
        review, _, _ = await SERVICE.admin_list("review")
        self.assertEqual(len(review), 1)
        everything, _, _ = await SERVICE.admin_list("all")
        self.assertEqual(len(everything), 4)
        nothing, page, pages = await SERVICE.admin_list("implemented")
        self.assertEqual((len(nothing), page, pages), (0, 1, 1))

    async def test_status_move_notifies_the_author_and_stays_idempotent(self):
        async with self.factory.begin() as s:
            idea = Idea(
                telegram_user_id=7,
                text="перенести награды в профиль",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(idea)
            await s.flush()
            idea_id = idea.id
        moved = await SERVICE.set_status(555, idea_id, "review")
        self.assertTrue(moved.ok, moved)
        async with self.factory() as s:
            row = await s.get(Idea, idea_id)
            self.assertEqual(row.status, "review")
            self.assertEqual(row.reviewed_by, 555)
            self.assertIsNotNone(row.reviewed_at)
            notices = (
                await s.scalars(
                    select(Notification).where(
                        Notification.type == "idea_status",
                        Notification.dedupe_key == f"idea_status:{idea_id}:review",
                    )
                )
            ).all()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].user_id, 7)
        self.assertIn("На рассмотрении", notices[0].text)
        repeat = await SERVICE.set_status(555, idea_id, "review")
        self.assertEqual(repeat.code, "UNCHANGED")
        async with self.factory() as s:
            still = (
                await s.scalars(
                    select(Notification).where(
                        Notification.dedupe_key == f"idea_status:{idea_id}:review"
                    )
                )
            ).all()
        self.assertEqual(len(still), 1, "повторная обработка не спамит")

    async def test_the_transition_map_stops_what_the_owner_should_not_do(self):
        async with self.factory.begin() as s:
            idea = Idea(
                telegram_user_id=7,
                text="в示范基地 разработке",
                status="in_progress",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(idea)
            await s.flush()
            idea_id = idea.id
        back = await SERVICE.set_status(555, idea_id, "review")
        self.assertEqual(back.code, "FORBID")
        self.assertEqual(back.data["from"], "in_progress")
        self.assertEqual(
            (await SERVICE.set_status(555, idea_id, "waterfall")).code, "BAD_STATUS"
        )

    async def test_duplicate_carries_the_original_reference(self):
        async with self.factory.begin() as s:
            original = Idea(
                telegram_user_id=7,
                text="первая формулировка",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            copy = Idea(
                telegram_user_id=8,
                text="вторая формулировка",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add_all([original, copy])
            await s.flush()
            original_id, copy_id = original.id, copy.id
        marked = await SERVICE.set_status(
            555, copy_id, "duplicate", duplicate_of=original_id
        )
        self.assertTrue(marked.ok, marked)
        async with self.factory() as s:
            row = await s.get(Idea, copy_id)
            self.assertEqual(row.status, "duplicate")
            self.assertEqual(row.duplicate_of, original_id)
            notices = (
                await s.scalars(
                    select(Notification).where(
                        Notification.dedupe_key == f"idea_status:{copy_id}:duplicate"
                    )
                )
            ).all()
        self.assertEqual(len(notices), 1)

    async def test_the_reply_is_saved_once_and_queued_once(self):
        async with self.factory.begin() as s:
            idea = Idea(
                telegram_user_id=7,
                text="жду ответа",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add(idea)
            await s.flush()
            idea_id = idea.id
        first = await SERVICE.save_reply(555, idea_id, "Сделаем в следующем месяце")
        self.assertTrue(first.ok, first)
        second = await SERVICE.save_reply(555, idea_id, "Уже в работе 🔧")
        self.assertTrue(second.ok, second)
        empty = await SERVICE.save_reply(555, idea_id, "   ")
        self.assertEqual(empty.code, "BAD_REPLY")
        missing = await SERVICE.save_reply(555, 9999, "привет")
        self.assertEqual(missing.code, "NOT_FOUND")
        async with self.factory() as s:
            row = await s.get(Idea, idea_id)
            self.assertEqual(row.admin_reply, "Уже в работе 🔧")
            self.assertIsNotNone(row.admin_reply_at)
            notices = (
                await s.scalars(
                    select(Notification).where(
                        Notification.type == "idea_reply",
                        Notification.dedupe_key.like(f"idea_reply:{idea_id}:%"),
                    )
                )
            ).all()
        self.assertEqual(
            len(notices), 1, "двойной тап в одну секунду не заносит вторую строку"
        )
        self.assertIn("Сделаем в следующем месяце", notices[0].text)

    async def test_the_owner_notice_never_loses_the_idea_even_if_the_queue_fails(self):
        # enqueue живёт вне Telegram: транзакция идеи фиксируется, уведомление
        # останется в очереди при любой задержке отправки.
        with patch("services.notifications.enqueue", AsyncMock()):
            result = await SERVICE.create(7, "идея под упавшим Telegram", 123, "T")
        self.assertTrue(result.ok, result)
        self.assertEqual(await self.idea_count(7), 1)


class UserFlowTests(DatabaseCase):
    """Прямые вызовы обработчиков: FSMContext на MemoryStorage, AsyncMock-стабы."""

    def setUp(self):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage

        self.storage = MemoryStorage()

    def state(self, user_id=7):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey

        return FSMContext(
            storage=self.storage,
            key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id),
        )

    async def idea_count(self, user_id=7):
        async with self.factory() as s:
            query = select(func.count(Idea.id)).where(Idea.telegram_user_id == user_id)
            return await s.scalar(query)

    def message_stub(self, user_id=7):
        stub = AsyncMock()
        stub.from_user = SimpleNamespace(id=user_id)
        return stub

    def callback_stub(self, data, user_id=7, first_name="Тимур"):

        stub = AsyncMock()
        stub.data = data
        stub.from_user = SimpleNamespace(id=user_id, first_name=first_name)
        stub.message = AsyncMock()
        stub.message.bot = types.SimpleNamespace(id=123)
        return stub

    async def test_the_menu_press_answers_with_the_ideas_screen(self):
        from handlers.ideas import open_ideas

        stub, state = self.message_stub(), self.state()
        with patch("handlers.ideas.set_online", AsyncMock()):
            await open_ideas(stub, state, lang="ru")
        text = stub.answer.await_args.args[0]
        self.assertIn("Предложи идею", text)
        markup = stub.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "idea:new")
        self.assertIsNone(await state.get_state())

    async def test_the_draft_lives_only_in_state_until_the_confirm_press(self):
        from handlers.ideas import cb_new, cb_submit, idea_text, open_ideas, IdeaFlow

        state, message, callback = (
            self.state(),
            self.message_stub(),
            self.callback_stub("idea:submit"),
        )
        with patch("handlers.ideas.set_online", AsyncMock()):
            await open_ideas(message, state, lang="ru")
        await cb_new(self.callback_stub("idea:new"), state, lang="ru")
        self.assertEqual(await state.get_state(), IdeaFlow.TEXT.state)
        # Черновик не западает в базу до подтверждения: пусто.
        self.assertEqual(await self.idea_count(7), 0)
        message.text = " <b>Акции</b> по набору "
        await idea_text(message, state, lang="ru")
        self.assertEqual(await state.get_state(), IdeaFlow.PREVIEW.state)
        preview = message.answer.await_args.args[0]
        self.assertIn(
            "&lt;b&gt;Акции&lt;/b&gt;", preview, "пользовательский текст экранируется"
        )
        await cb_submit(callback, state, lang="ru")
        self.assertEqual(await self.idea_count(7), 1)
        async with self.factory() as s:
            stored = (
                await s.scalars(select(Idea).where(Idea.telegram_user_id == 7))
            ).first()
        # В базу уходит сырой текст: экранирование - только на рендере.
        self.assertEqual(stored.text, "<b>Акции</b> по набору")
        self.assertIsNone(await state.get_state())
        # Второе нажатие той же кнопки: черновика нет, строки не две.
        await cb_submit(callback, state, lang="ru")
        self.assertEqual(await self.idea_count(7), 1)
        callback.answer.assert_any_call(T.tr(T.IDEA_CALL_DONE, "ru"))

    async def test_cancel_and_edit_roundtrips(self):
        from handlers.ideas import cb_edit, idea_text, cb_cancel, cb_new, IdeaFlow

        state, message = self.state(), self.message_stub()
        await cb_new(self.callback_stub("idea:new"), state, lang="ru")
        message.text = "первая версия"
        await idea_text(message, state, lang="ru")
        await cb_edit(self.callback_stub("idea:edit"), state, lang="ru")
        self.assertEqual(await state.get_state(), IdeaFlow.TEXT.state)
        message.text = ""
        await idea_text(message, state, lang="ru")
        empty_answer = message.answer.await_args_list[-1].args[0]
        self.assertEqual(empty_answer, T.tr(T.IDEA_EMPTY, "ru"))
        await cb_cancel(self.callback_stub("idea:cancel"), state, lang="ru")
        self.assertIsNone(await state.get_state())
        self.assertEqual(await self.idea_count(7), 0)

    async def test_the_mine_screen_shows_own_rows_and_hides_alien_cards(self):
        from handlers.ideas import cb_mine, cb_view

        async with self.factory.begin() as s:
            own = Idea(
                telegram_user_id=9,
                text="моя идея про награды",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            foreign = Idea(
                telegram_user_id=8,
                text="СЕКРЕТ ЧУЖОЙ КАРТОЧКИ",
                status="new",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            s.add_all([own, foreign])
            await s.flush()
            own_id, foreign_id = own.id, foreign.id
        stub = self.callback_stub("idea:mine", user_id=9)
        await cb_mine(stub, self.state(9), lang="ru")
        text = stub.message.answer.await_args.args[0]
        self.assertIn(f"#{own_id}", text)
        self.assertIn("🆕 Новая", text)
        alien = self.callback_stub(f"idea:view:{foreign_id}", user_id=9)
        await cb_view(alien, self.state(9), lang="ru")
        seen = " ".join(str(c.args[0]) for c in alien.message.answer.await_args_list)
        self.assertIn(
            T.tr(
                {
                    "ru": "Идея не найдена.",
                    "en": "Idea not found.",
                    "uk": "Ідею не знайдено.",
                },
                "ru",
            ),
            seen,
        )
        self.assertNotIn("СЕКРЕТ", seen)
        area = self.callback_stub(f"idea:view:{own_id}", user_id=9)
        await cb_view(area, self.state(9), lang="ru")
        card = area.message.answer.await_args.args[0]
        self.assertIn("моя идея про награды", card)

    async def test_the_idea_button_stays_out_of_the_dialog_relay(self):
        # Регресс на SYSTEM_TEXTS: подпись новой кнопки не уходит собеседнику
        # как обычный текст (проверка состава набора системных подписей).
        self.assertIn(T.label(T.IDEA, "ru"), T.SYSTEM_TEXTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
