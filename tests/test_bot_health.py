"""Уведомления о мёртвом токене и о чужом поллинге.

Оба отказа Telegram не анонсирует: бот просто молчит, а aiogram зацикливает
ошибку и молчит тоже. Единственное место, где причина вообще видна, это лог
диспетчера и ответ get_me -- поэтому тесты здесь проверяют именно эти два
канала и то, как из них собирается страница владельцу.

Всё офлайн: ни одной реальной сети, ни одной настоящей БД.
"""
import asyncio
import logging
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from aiogram.exceptions import (TelegramBadRequest, TelegramConflictError,
                                TelegramUnauthorizedError)
from aiogram.methods import GetMe

from utils import bot_health


CONFLICT_ERROR = ("Failed to fetch updates - TelegramConflictError: "
                  "Conflict: terminated by other getUpdates request; make "
                  "sure that only one bot instance is running")
CONFLICT_RETRY = ("Sleep for 1.0 seconds and try again... (tryings = 16, "
                  "bot id = 7828301604)")


def record_of(message, level=logging.ERROR):
    return logging.LogRecord(name="aiogram.dispatcher", level=level,
                             pathname=__file__, lineno=1, msg=message,
                             args=(), exc_info=None)


class BotHealthCase(unittest.TestCase):
    def setUp(self):
        bot_health._hits.clear()
        bot_health._paged.clear()
        bot_health._pending_kind = None
        bot_health._last_probe = 0.0

    # -- счётчик ----------------------------------------------------------

    def test_few_errors_are_not_an_incident(self):
        for _ in range(bot_health.THRESHOLD - 1):
            bot_health.record(7828301604, "conflict")
        self.assertEqual(bot_health.drain(), [])

    def test_repeated_errors_become_an_incident(self):
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict")
        incidents = bot_health.drain()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0][0], 7828301604)
        self.assertEqual(incidents[0][1], "conflict")

    def test_errors_outside_the_window_fall_off(self):
        # Записи старше WINDOW перестают считаться: разовый сетевой сбой час
        # назад не делает текущий инцидент инцидентом.
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict")
        past = time.monotonic() + bot_health.WINDOW + 1
        self.assertEqual(bot_health.drain(now=past), [])

    def test_transient_errors_are_never_paged(self):
        for _ in range(bot_health.THRESHOLD * 3):
            bot_health.record(7828301604, None)
        self.assertEqual(bot_health.drain(), [])

    def test_two_bots_page_independently(self):
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict")
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(8873771495, "unauthorized")
        kinds = {bot_id: kind for bot_id, kind, _ in bot_health.drain()}
        self.assertEqual(kinds, {7828301604: "conflict",
                                 8873771495: "unauthorized"})

    # -- троттлинг --------------------------------------------------------

    def test_one_incident_pages_once_not_per_attempt(self):
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict")
        self.assertEqual(len(bot_health.drain()), 1)
        # Следующая попытка в пределах COOLDOWN не должна слать копию.
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict")
        self.assertEqual(bot_health.drain(), [])

    def test_incident_pages_again_after_the_cooldown(self):
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict")
        self.assertEqual(len(bot_health.drain()), 1)
        # Ошибки продолжают сыпаться: страница нужна снова, но не раньше
        # COOLDOWN. Час вперёд -- та же проверка без реальной паузы.
        past = time.monotonic() + bot_health.COOLDOWN + 1
        for _ in range(bot_health.THRESHOLD):
            bot_health.record(7828301604, "conflict", now=past)
        self.assertEqual(len(bot_health.drain(now=past)), 1)

    # -- лог диспетчера ---------------------------------------------------

    def test_watcher_pairs_the_error_line_with_the_bot_id(self):
        watcher = bot_health.DispatcherWatcher()
        watcher.emit(record_of(CONFLICT_ERROR))
        self.assertEqual(bot_health._pending_kind, "conflict")
        self.assertEqual(bot_health._hits, {})  # без id ещё не записываем
        watcher.emit(record_of(CONFLICT_RETRY, logging.WARNING))
        self.assertIn((7828301604, "conflict"), bot_health._hits)
        self.assertIsNone(bot_health._pending_kind)

    def test_watcher_ignores_unrelated_lines(self):
        watcher = bot_health.DispatcherWatcher()
        watcher.emit(record_of("Start polling"))
        watcher.emit(record_of("Failed to fetch updates - TelegramNetworkError: boom"))
        watcher.emit(record_of(CONFLICT_RETRY, logging.WARNING))
        # TelegramNetworkError -- транзиент, его никто не пингует.
        self.assertEqual(bot_health._hits, {})
        self.assertIsNone(bot_health._pending_kind)

    def test_watcher_does_not_leak_a_kind_into_the_next_bot(self):
        watcher = bot_health.DispatcherWatcher()
        watcher.emit(record_of(CONFLICT_ERROR))
        watcher.emit(record_of(CONFLICT_RETRY, logging.WARNING))
        # Вторая пара строк уже без ошибки -- kind не должен пережить первую.
        watcher.emit(record_of(CONFLICT_RETRY, logging.WARNING))
        self.assertEqual(len(bot_health._hits[(7828301604, "conflict")]), 1)

    def test_attach_puts_the_handler_on_the_dispatcher_logger(self):
        target = logging.getLogger("aiogram.dispatcher")
        before = list(target.handlers)
        try:
            bot_health.attach(SimpleDispatcher())
            self.assertEqual(len(target.handlers), len(before) + 1)
        finally:
            target.handlers[:] = before

    # -- get_me как второй канал ------------------------------------------

    def test_probe_pages_a_token_telegram_rejects(self):
        broken = AsyncMock()
        broken.id = 7828301604
        broken.get_me.side_effect = TelegramUnauthorizedError(
            method=GetMe(), message="Unauthorized")
        healthy = AsyncMock()
        healthy.id = 8873771495
        with patch.object(bot_health.bots, "all", return_value=(broken, healthy)):
            asyncio.run(bot_health.probe(force=True))
        self.assertIn((7828301604, "unauthorized"), bot_health._hits)
        self.assertEqual(bot_health._hits.get((8873771495, "unauthorized")), None)

    def test_probe_clears_a_token_that_came_back(self):
        broken = AsyncMock()
        broken.id = 7828301604
        broken.get_me.side_effect = TelegramUnauthorizedError(
            method=GetMe(),
            message="Unauthorized")
        with patch.object(bot_health.bots, "all", return_value=(broken,)):
            asyncio.run(bot_health.probe(force=True))
        self.assertIn((7828301604, "unauthorized"), bot_health._hits)
        # Токен завезли обратно -- старая запись не должна стрелять повтором.
        broken.get_me.side_effect = None
        broken.get_me.return_value = SimpleNamespace(id=7828301604)
        with patch.object(bot_health.bots, "all", return_value=(broken,)):
            asyncio.run(bot_health.probe(force=True))
        self.assertNotIn((7828301604, "unauthorized"), bot_health._hits)

    def test_probe_is_throttled(self):
        broken = AsyncMock()
        broken.id = 7828301604
        broken.get_me.side_effect = TelegramUnauthorizedError(
            method=GetMe(),
            message="Unauthorized")
        with patch.object(bot_health.bots, "all", return_value=(broken,)) as all_:
            asyncio.run(bot_health.probe(force=True))
            asyncio.run(bot_health.probe())
            self.assertEqual(all_.call_count, 1)

    # -- классификация ----------------------------------------------------

    def test_classify_names_only_the_failures_a_human_fixes(self):
        method = GetMe()
        self.assertEqual(
            bot_health.classify(TelegramConflictError(
                method=method, message="Conflict")), "conflict")
        self.assertEqual(
            bot_health.classify(TelegramUnauthorizedError(
                method=method, message="Unauthorized")), "unauthorized")
        self.assertIsNone(bot_health.classify(TelegramBadRequest(
            method=method, message="Bad Request: chat not found")))

    # -- текст уведомления ------------------------------------------------

    def test_notice_names_the_bot_and_the_reason(self):
        text = bot_health.notice_text(7828301604, "conflict", 12)
        self.assertIn("7828301604", text)
        self.assertIn("опрашивает кто-то ещё", text)
        self.assertIn("12", text)
        self.assertIn("@BotFather", bot_health.notice_text(
            7828301604, "unauthorized", 1))


class SimpleDispatcher:
    """Прокидка: attach() не смотрит на диспетчер, ему нужен любой объект."""


if __name__ == "__main__":
    unittest.main()
