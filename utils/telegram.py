import asyncio
import logging

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates
from aiogram.types import InputFile

logger = logging.getLogger(__name__)

# Канал до api.telegram.org рвёт соединения посреди TLS (Request timeout /
# Connection reset by peer): раньше такой обрыв стоил пользователю 20-30
# секунд немоты, и повтора не было. Короткий потолок превращает зависание
# в быструю неудачу, а RetryTelegramRequests повторяет запрос сразу после неё.
REQUEST_TIMEOUT = 12.0


class TimeoutBoundedSession(AiohttpSession):
    """Короткий потолок для мелких вызовов; запас -- загрузкам и опросу.

    Обычный ответ API приходит за доли секунды, поэтому 12 секунд -- это
    заведомо быстрая неудача, а не подгонка. Загрузку файла (кросс-бот
    перелив медиа) и long polling так резать нельзя: видео на слабом
    аплинке отправляется дольше, а ожидание апдейта длится до 10 секунд.
    """

    async def make_request(self, bot, method, timeout=None):
        bounded = not isinstance(method, GetUpdates) and not _carries_files(method)
        if timeout is None and bounded:
            timeout = REQUEST_TIMEOUT
        return await super().make_request(bot, method, timeout=timeout)


def _carries_files(method):
    """Признак загрузки: в дампе метода есть вложенный InputFile."""
    return _contains_file(method.model_dump(warnings=False))


def _contains_file(value):
    if isinstance(value, InputFile):
        return True
    if isinstance(value, dict):
        return any(_contains_file(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_file(item) for item in value)
    return False


class RetryTelegramRequests(BaseRequestMiddleware):
    """Повторяет транспортные сбои; ошибки бизнес-логики идут наверх."""

    ATTEMPTS = 3
    NETWORK_BACKOFF = (0.5, 1.5)

    async def __call__(self, make_request, bot, method):
        # Повтор после обрыва может доставить сообщение дважды, если запрос
        # дошёл до Telegram, а потерялся только ответ. Для ретрансляции
        # диалога редкий дубль лучше молчания -- молчание рвёт разговор.
        network_attempts = 0
        for attempt in range(self.ATTEMPTS):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as error:
                if attempt == self.ATTEMPTS - 1 or error.retry_after > 60:
                    raise
                await asyncio.sleep(error.retry_after)
            except TelegramNetworkError:
                # Long polling восстанавливается на уровне диспетчера:
                # ожидания внутри одного запроса только растянут цикл.
                if isinstance(method, GetUpdates) or attempt == self.ATTEMPTS - 1:
                    raise
                last = len(self.NETWORK_BACKOFF) - 1
                delay = self.NETWORK_BACKOFF[min(network_attempts, last)]
                network_attempts += 1
                logger.warning(
                    "event=telegram_network_retry method=%s attempt=%d delay=%.1f",
                    type(method).__name__,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
