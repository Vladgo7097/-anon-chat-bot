import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from models.base import engine, Base
from services.chat_manager import close_redis
from services.notifications import notification_loop
from utils.middleware import AntiFloodMiddleware

logger = logging.getLogger(__name__)


async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")


async def on_shutdown():
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete")


async def main():
    if not config.BOT_TOKEN:
        print("BOT_TOKEN not set! Create .env file.")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    await on_startup()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    from handlers import load_routers

    dp.include_routers(*load_routers())

    dp.message.middleware(AntiFloodMiddleware())

    # Start background notification loop
    asyncio.create_task(notification_loop(bot))

    logger.info("Bot starting...")

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped")
