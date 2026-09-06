import asyncio
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter


class RetryTelegramRequests(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        for attempt in range(3):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as error:
                if attempt == 2 or error.retry_after > 60:
                    raise
                await asyncio.sleep(error.retry_after)
