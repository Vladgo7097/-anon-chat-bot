"""Property-based проверки на границах, которые примерные тесты не покрывают.

Три инварианта, выбранные не случайно:

1. RetryTelegramRequests повторяет запрос при TelegramRetryAfter, пока
   retry_after не превысил 60 секунд и попытки не кончились. Тут легко
   ошибиться на границе: 60 ретраится, 61 -- уже нет, а на третьей
   попытке возврат бросается при любом retry_after.

2. UpdateDeduplication дропает повторную доставку одного update_id и
   никогда не дропает первый. Повторное обновление -- норма для polling
   (глюк сети, рестарт), и молча потерянный первый зов = потерянное
   сообщение пользователя.

3. callback_data каждой кнопки укладывается в лимит Telegram в 64 байта.
   Кнопки собираются f-строками из id вроде report_id, offset и
   telegram_id: превышение вылезает не в тесте, а в живом боте, где
   Telegram молча отклоняет нажатие.

Async-сценарии обёрнуты в asyncio.run(), а не в IsolatedAsyncioTestCase:
Hypothesis не умеет разворачивать coroutine-тесты самостоятельно, и
такая обёртка не требует конфигурации pytest-asyncio.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiogram.exceptions import TelegramRetryAfter  # noqa: E402
from redis.exceptions import ConnectionError as RedisConnectionError  # noqa: E402

from keyboards.inline import (  # noqa: E402
    admin_audit_kb,
    admin_growth_kb,
    admin_report_kb,
    admin_user_list_kb,
)
from utils.deduplication import UpdateDeduplication  # noqa: E402
from utils.telegram import RetryTelegramRequests  # noqa: E402

TELEGRAM_CALLBACK_LIMIT_BYTES = 64
# Telegram ids stay inside 52 bits; larger ids cannot exist on the platform.
TELEGRAM_ID_MAX = 2**52 - 1


class RetryPropertyTests(unittest.TestCase):
    """Граница retry_after: 60 ретраится, 61 -- немедленный отказ."""

    @given(retry_after=st.integers(min_value=0, max_value=59))
    @settings(max_examples=25, deadline=None)
    def test_transient_retry_after_is_retried_until_success(self, retry_after):
        """Малый retry_after трижды: возврат на третьей попытке."""
        calls = []

        async def make_request(bot, method):
            calls.append(1)
            if len(calls) < 3:
                raise TelegramRetryAfter(
                    method=method, message="slow down", retry_after=retry_after
                )
            return "ok"

        async def scenario():
            middleware = RetryTelegramRequests()
            with patch("utils.telegram.asyncio.sleep", new=AsyncMock()) as sleep:
                result = await middleware(make_request, object(), object())
            return result, sleep

        result, sleep = asyncio.run(scenario())

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.await_count, 2)

    @given(retry_after=st.integers(min_value=61, max_value=10_000))
    @settings(max_examples=25, deadline=None)
    def test_large_retry_after_is_raised_without_sleeping(self, retry_after):
        """Большой retry_after: одна попытка, без ожидания, ошибка вверх."""
        calls = []

        async def make_request(bot, method):
            calls.append(1)
            raise TelegramRetryAfter(
                method=method, message="too long", retry_after=retry_after
            )

        async def scenario():
            middleware = RetryTelegramRequests()
            with patch("utils.telegram.asyncio.sleep", new=AsyncMock()) as sleep:
                with self.assertRaises(TelegramRetryAfter):
                    await middleware(make_request, object(), object())
            return sleep

        sleep = asyncio.run(scenario())

        self.assertEqual(len(calls), 1)
        sleep.assert_not_awaited()

    @given(retry_after=st.integers(min_value=0, max_value=59))
    @settings(max_examples=25, deadline=None)
    def test_exhausted_attempts_propagate_the_last_error(self, retry_after):
        """Три неудачи подряд: ошибка уходит наверх, а не проглатывается."""
        calls = []

        async def make_request(bot, method):
            calls.append(1)
            raise TelegramRetryAfter(
                method=method, message="still slow", retry_after=retry_after
            )

        async def scenario():
            middleware = RetryTelegramRequests()
            with patch("utils.telegram.asyncio.sleep", new=AsyncMock()):
                with self.assertRaises(TelegramRetryAfter):
                    await middleware(make_request, object(), object())

        asyncio.run(scenario())

        self.assertEqual(len(calls), 3)


class DedupPropertyTests(unittest.TestCase):
    """Первая доставка обрабатывается, повторная -- дропается."""

    @given(update_id=st.integers(min_value=0, max_value=2**31 - 1))
    @settings(max_examples=25, deadline=None)
    def test_second_delivery_of_the_same_update_is_dropped(self, update_id):
        delivered = set()

        async def scenario():
            middleware = UpdateDeduplication()
            redis = AsyncMock()
            redis.exists = AsyncMock(side_effect=lambda key: key in delivered)

            async def mark_done(key, value, ex=None):
                delivered.add(key)

            redis.set = AsyncMock(side_effect=mark_done)
            handler = AsyncMock(return_value="handled")
            event = SimpleNamespace(update_id=update_id)
            data = {"bot": SimpleNamespace(id=1)}

            with patch("utils.deduplication.get_redis", AsyncMock(return_value=redis)):
                first = await middleware(handler, event, data)
                second = await middleware(handler, event, data)
            return first, second, handler

        first, second, handler = asyncio.run(scenario())

        self.assertEqual(first, "handled")
        self.assertIsNone(second)
        self.assertEqual(handler.await_count, 1)

    @given(update_id=st.integers(min_value=0, max_value=2**31 - 1))
    @settings(max_examples=25, deadline=None)
    def test_update_is_still_handled_when_redis_is_down(self, update_id):
        """Redis упал: дедуп молчит, но обновление обработать всё равно надо."""

        async def scenario():
            middleware = UpdateDeduplication()
            redis = AsyncMock()
            redis.exists = AsyncMock(side_effect=RedisConnectionError("down"))
            handler = AsyncMock(return_value="handled")
            event = SimpleNamespace(update_id=update_id)
            data = {"bot": SimpleNamespace(id=1)}

            with patch("utils.deduplication.get_redis", AsyncMock(return_value=redis)):
                with self.assertLogs("utils.deduplication", level="WARNING"):
                    result = await middleware(handler, event, data)
            return result, handler

        result, handler = asyncio.run(scenario())

        self.assertEqual(result, "handled")
        self.assertEqual(handler.await_count, 1)


class CallbackDataLimitTests(unittest.TestCase):
    """Ни одна кнопка не вылезает за 64 байта, какие бы id в неё не положили."""

    @given(
        telegram_id=st.integers(min_value=0, max_value=TELEGRAM_ID_MAX),
        report_id=st.integers(min_value=0, max_value=2**31 - 1),
        offset=st.integers(min_value=0, max_value=10_000),
    )
    @settings(max_examples=50, deadline=None)
    def test_every_generated_callback_data_fits_telegram_limit(
        self, telegram_id, report_id, offset
    ):
        keyboards = [
            admin_user_list_kb([(telegram_id, "Иван")], "admin:sec:users"),
            admin_report_kb(report_id, offset),
            admin_audit_kb(offset, 15, True),
            admin_growth_kb(1),
        ]

        for keyboard in keyboards:
            for row in keyboard.inline_keyboard:
                for button in row:
                    data = button.callback_data
                    if data is None:
                        continue
                    size = len(data.encode("utf-8"))
                    self.assertLessEqual(
                        size,
                        TELEGRAM_CALLBACK_LIMIT_BYTES,
                        f"{data!r} занимает {size} байт при лимите "
                        f"{TELEGRAM_CALLBACK_LIMIT_BYTES}: Telegram отклонит нажатие",
                    )


if __name__ == "__main__":
    unittest.main()
