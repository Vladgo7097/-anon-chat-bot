"""Container liveness and dependency checks; never calls Telegram."""
import asyncio
import time
from pathlib import Path

from sqlalchemy import text
from models.base import engine
from services.chat_manager import get_redis, close_redis


async def check():
    heartbeat = Path("/tmp/anon-worker-heartbeat")
    if not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > 120:
        raise RuntimeError("Worker heartbeat expired")
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await (await get_redis()).ping()
    finally:
        await close_redis()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(check(), timeout=8))
