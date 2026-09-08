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
        await asyncio.sleep(0.7)
        # Close the collection window before handing off, not after the handler
        # returns: forward_message can take a while (copy_messages, DB writes),
        # and an item arriving during that window used to land in this
        # already-consumed list and never reach any handler at all. Popping
        # first means a late straggler starts its own (separate) album instead
        # of being silently dropped.
        self.pending.pop(key, None)
        data["album"] = sorted(items, key=lambda m: m.message_id)
        return await handler(event, data)
