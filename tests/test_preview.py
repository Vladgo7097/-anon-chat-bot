"""Preview-режим: карточка, kill-switch, согласие/отказ, резервации.

Локальный класс -- машинерия без Telegram/Redis/Postgres (SQLite
создаёт все нужные таблицы через metadata.create_all; redis -- AsyncMock
с dict-состоянием). Класс PreviewPgTests -- полный машинный путь против
изолированного PostgreSQL/Redis: включается RUN_POSTGRES_TESTS=1
(тест-стек сервера).
"""

import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from test_chat_duration import DatabaseCase
from models.user import User
from utils.time import utcnow

import models.preview  # noqa: F401 -- side-effect регистрация таблицы
from models.preview import PreviewOffer
from models.chat import ActiveParticipant


class _FakeSettingSession:
    """Заглушка async_session: и есть сессия, и .get() отдаёт настройку."""

    def __init__(self, setting):
        self.setting = setting
        self.sessions = 0

    def __call__(self):
        self.sessions += 1
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, cls, key):
        return self.setting


class FlagTests(unittest.IsolatedAsyncioTestCase):
    """Kill-switch preview_mode: BotSetting 0/1 с кэшем 5 c."""

    def setUp(self):
        from services import preview as preview_module

        self.preview_module = preview_module
        preview_module._FLAG.update(value=False, expires=0.0)

    async def test_setting_missing_means_off(self):
        cm = _FakeSettingSession(None)
        with patch("services.preview.async_session", cm):
            self.assertFalse(await self.preview_module.enabled())
        self.assertEqual(cm.sessions, 1)

    async def test_flag_cached_across_calls(self):
        cm = _FakeSettingSession(type("Setting", (), {"value_json": {"value": 1}})())
        with patch("services.preview.async_session", cm):
            self.assertTrue(await self.preview_module.enabled())
            self.assertTrue(await self.preview_module.enabled())
        # Второй вызов обязан упасть в кэш, не ходить в базу.
        self.assertEqual(cm.sessions, 1)

    async def test_broken_value_is_off(self):
        cm = _FakeSettingSession(type("Setting", (), {"value_json": {}})())
        with patch("services.preview.async_session", cm):
            self.assertFalse(await self.preview_module.enabled())


class CardTextTests(unittest.TestCase):
    """Чистые тексты: подпись, усечение, кнопки без иконок."""

    def _user(self, **kw):
        defaults = dict(
            telegram_id=1,
            anon_id="#48213",
            gender="male",
            age=25,
            bio="Обо мне",
        )
        defaults.update(kw)
        return User(**defaults)

    def test_caption_uses_texts_template(self):
        from handlers.preview import preview_caption

        caption = preview_caption(self._user(), "ru")
        self.assertIn("#48213", caption)
        self.assertIn("25", caption)
        self.assertIn("парень", caption)
        self.assertIn("Обо мне", caption)
        self.assertNotIn("{anon}", caption)

    def test_female_label(self):
        from handlers.preview import preview_caption

        self.assertIn("девушка", preview_caption(self._user(gender="female"), "ru"))

    def test_bio_truncated(self):
        from handlers.preview import preview_caption

        caption = preview_caption(self._user(bio="x" * 500), "ru")
        self.assertLess(len(caption), 500)
        self.assertTrue(caption.endswith("…"))
        self.assertNotIn("x" * 400, caption)

    def test_empty_bio_no_trailing_break(self):
        from handlers.preview import preview_caption

        caption = preview_caption(self._user(bio=None), "ru")
        self.assertFalse(caption.endswith("\n"))

    def test_keyboard_buttons_without_icons(self):
        from handlers.preview import preview_request_keyboard

        keyboard = preview_request_keyboard(7, "ru")
        (row,) = keyboard.inline_keyboard
        self.assertEqual(
            [(button.text, button.callback_data) for button in row],
            [("Подошло", "pv:yes:7"), ("Не сейчас", "pv:no:7")],
        )
        # Правило каталога эмодзи: новая кнопка -- без icon_custom_emoji_id,
        # пока dj не выдал doc-id.
        self.assertTrue(all(button.icon_custom_emoji_id is None for button in row))


class PreviewServiceTests(DatabaseCase):
    """Конечные автоматы согласия/отказа/подметания на локальных таблицах."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from repositories.chats import ChatRepository

        self.repo = ChatRepository()
        p = patch(
            "services.chat_manager.get_redis",
            AsyncMock(return_value=self._mock_redis()),
        )
        p.start()
        self.addCleanup(p.stop)
        p = patch("services.preview.async_session", self.factory)
        p.start()
        self.addCleanup(p.stop)
        p = patch("services.settings.bot_value", AsyncMock(return_value=60))
        p.start()
        self.addCleanup(p.stop)
        # Юзеры 1 (male), 2 (female), 3 (male) уже созданы DatabaseCase:
        # ними и живём.

    @staticmethod
    def _mock_redis():
        """zscore/exists живут по фактическому наличию id в очереди."""
        queue = {"1", "2", "3"}
        r = AsyncMock()

        async def zscore(key, uid):
            return 1 if str(uid) in queue else None

        async def exists(key, *args):
            if key.startswith("search:user:"):
                return True
            return False

        r.zscore = zscore
        r.exists = exists
        return r

    async def test_create_offer_persists_pair_and_cards(self):
        from models.operations import Notification

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        self.assertEqual(offer.status, "requested")
        self.assertEqual(sorted((offer.user1_id, offer.user2_id)), [1, 2])
        self.assertTrue(offer.expires_at > utcnow())
        async with self.factory() as s:
            cards = (
                await s.scalars(
                    select(Notification).where(
                        Notification.type == "preview",
                        Notification.callback_data == str(offer.id),
                    )
                )
            ).all()
        self.assertEqual(sorted(card.user_id for card in cards), [1, 2])
        # Контент собирается воркером в момент доставки, поэтому пуст.
        self.assertTrue(all(card.text == "" for card in cards))

    async def test_offer_reserves_pair_against_third_party(self):
        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        self.assertIsNotNone(offer)
        self.assertEqual(await self.repo.create_offer(2, 3, self._mock_redis()), None)

    async def test_reserved_users_seen(self):
        await self.repo.create_offer(1, 2, self._mock_redis())
        async with self.factory() as s:
            reserved = await self.repo.reserved_users(s, {1, 2, 3})
        self.assertEqual(reserved, {1, 2})

    async def test_consent_single_side_waits(self):
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        state, chat = await PreviewService.consent(offer.user1_id, offer.id)
        self.assertEqual(state, "waited")
        self.assertIsNone(chat)
        async with self.factory() as s:
            row = await s.get(PreviewOffer, offer.id)
            self.assertEqual(row.status, "requested")
            self.assertTrue(row.consent_1 or row.consent_2)

    async def test_consent_both_sides_open_chat(self):
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        state, chat = await PreviewService.consent(offer.user2_id, offer.id)
        self.assertEqual(state, "waited")
        state, chat = await PreviewService.consent(offer.user1_id, offer.id)
        self.assertEqual(state, "mutual")
        self.assertIsNotNone(chat)
        async with self.factory() as s:
            row = await s.get(PreviewOffer, offer.id)
            self.assertEqual(row.status, "mutual")
            self.assertEqual(row.resolved_session, chat.session_id)
            participant = await s.scalar(
                select(ActiveParticipant).where(
                    ActiveParticipant.session_id == chat.session_id
                )
            )
            self.assertIsNotNone(participant)

    async def test_decline_releases_pair(self):
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        state = await PreviewService.decline(offer.user2_id, offer.id)
        self.assertEqual(state, "declined")
        async with self.factory() as s:
            row = await s.get(PreviewOffer, offer.id)
            self.assertEqual(row.status, "declined")
            self.assertIsNotNone(row.resolved_at)
            self.assertEqual(await self.repo.reserved_users(s, {1, 2}), set())

    async def test_decline_cools_pair_on_candidates_layer(self):
        """Кулдаун отказа живёт в candidates-слое: свежий кандидат
        вытесняет недавно отклонённого, узкий пул не подвешиваем."""
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        await PreviewService.decline(offer.user2_id, offer.id)
        async with self.factory() as s:
            recent = await self.repo.recent_partners(s, 1, [2])
        self.assertIn(2, recent)
        # Пара из двух кандидатов: 2 в кулдауне -- остаётся 3.
        candidates = await self.repo.compatible_candidates(1, [2, 3])
        self.assertEqual(candidates, [3])

    async def test_decline_by_stranger_rejected(self):
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        self.assertEqual(await PreviewService.decline(3, offer.id), "gone")
        self.assertEqual(await PreviewService.decline(999, offer.id), "gone")

    async def test_cancel_active_closes_offers(self):
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        self.assertEqual(await PreviewService.cancel_active(1), 1)
        async with self.factory() as s:
            row = await s.get(PreviewOffer, offer.id)
            self.assertEqual(row.status, "canceled")

    async def test_expire_stale_sweeps_only_expired(self):
        from services.preview import PreviewService

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        async with self.factory.begin() as s:
            row = await s.get(PreviewOffer, offer.id)
            row.expires_at = utcnow() - timedelta(seconds=5)
        self.assertEqual(await PreviewService.expire_stale(), 1)
        async with self.factory() as s:
            row = await s.get(PreviewOffer, offer.id)
            self.assertEqual(row.status, "expired")
            self.assertIsNotNone(row.resolved_at)
        # Живой оффер sweeper не трогает (пара 1-3 -- другая строка:
        # на SQLite partial-индекс материализуется полным, дубликат (1,2)
        # обязателен только на PostgreSQL).
        fresh = await self.repo.create_offer(1, 3, self._mock_redis())
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh.status, "requested")
        self.assertEqual(await PreviewService.expire_stale(), 0)

    async def test_cleanup_prunes_closed_offers_only(self):
        from services.preview import PreviewService, cleanup

        offer = await self.repo.create_offer(1, 2, self._mock_redis())
        await PreviewService.decline(offer.user2_id, offer.id)
        # Свежее закрытие режется только по недельному дедлайну.
        self.assertEqual(await cleanup(), 0)
        async with self.factory.begin() as s:
            row = await s.get(PreviewOffer, offer.id)
            row.resolved_at = utcnow() - timedelta(days=8)
            row.created_at = utcnow() - timedelta(days=9)
        self.assertEqual(await cleanup(), 1)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_TESTS") == "1", "isolated PostgreSQL/Redis only"
)
class PreviewPgTests(DatabaseCase):
    """Полный машинный путь: matcher -> оффер -> резервация -> тумблер."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        import redis.asyncio as redis

        self.real_redis = redis.from_url(
            "redis://test-redis:6379/0", decode_responses=True
        )
        await self.real_redis.flushdb()
        p = patch("services.chat_manager.get_redis", return_value=self.real_redis)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.real_redis.aclose()
        await super().asyncTearDown()

    async def _queue_off_pair(self, first, second):
        await self.real_redis.zadd(
            "search_queue", {str(first): 100.0, str(second): 200.0}
        )
        for uid in (first, second):
            await self.real_redis.set(f"search:user:{uid}", "1", ex=120)

    async def test_matcher_creates_offer_instead_of_chat(self):
        from services.matching import MatchingService
        from services import preview as preview_module

        await self._queue_off_pair(1, 3)
        with patch.object(preview_module, "enabled", AsyncMock(return_value=True)):
            result = await MatchingService().find(1)
        self.assertIsNone(result)
        async with self.factory() as s:
            offer = await s.scalar(
                select(PreviewOffer).where(PreviewOffer.status == "requested")
            )
        self.assertIsNotNone(offer)
        self.assertEqual(sorted((offer.user1_id, offer.user2_id)), [1, 3])
        # Second request -- повторных офферов нет: pair удержан.
        with patch.object(preview_module, "enabled", AsyncMock(return_value=True)):
            self.assertIsNone(await MatchingService().find(3))
        async with self.factory() as s:
            offers = (await s.scalars(select(PreviewOffer))).all()
        self.assertEqual(len(offers), 1)

    async def test_reservation_survives_flag_flips_off(self):
        from services.matching import MatchingService
        from services import preview as preview_module
        from models.chat import Chat

        await self._queue_off_pair(1, 3)
        with patch.object(preview_module, "enabled", AsyncMock(return_value=True)):
            await MatchingService().find(1)
        # Тумблер выключен, но резервация держит пару от match().
        with patch.object(preview_module, "enabled", AsyncMock(return_value=False)):
            self.assertIsNone(await MatchingService().find(1))
        async with self.factory() as s:
            chats = (await s.scalars(select(Chat))).all()
        self.assertEqual(chats, [])
