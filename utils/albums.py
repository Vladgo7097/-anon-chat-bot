import asyncio
from aiogram import BaseMiddleware


class AlbumMiddleware(BaseMiddleware):
    """Collect only relay albums before the per-action flood limiter."""
    def __init__(self):
        self.pending = {}

    async def __call__(self, handler, event, data):
        if not event.media_group_id or data["handler"].callback.__name__ != "forward_message":
            return await handler(event, data)
        key = (event.chat.id, event.media_group_id)
        if key in self.pending:
            self.pending[key].append(event)
            return
        items = self.pending[key] = [event]
        try:
            await asyncio.sleep(0.7)
            data["album"] = sorted(items, key=lambda m: m.message_id)
            return await handler(event, data)
        finally:
            self.pending.pop(key, None)
