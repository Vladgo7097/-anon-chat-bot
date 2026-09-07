"""Queue an owner alert when the host database backup unit fails."""
import asyncio
from datetime import datetime, timezone

import config
from models.base import async_session, engine
from services.notifications import enqueue


async def notify_failure():
    if not config.OWNER_ID:
        return
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    async with async_session.begin() as session:
        await enqueue(session, config.OWNER_ID, "backup_failed", f"backup_failed:{hour}",
                      "⚠️ Не удалось создать или проверить резервную копию базы данных.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(notify_failure())
