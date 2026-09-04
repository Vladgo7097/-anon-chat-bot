from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import Message
from services.chat_manager import check_flood


class AntiFloodMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.from_user and await check_flood(event.from_user.id):
            return None
        return await handler(event, data)
