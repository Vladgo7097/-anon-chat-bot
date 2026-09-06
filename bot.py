import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

import config
from models.base import engine, Base
from services.chat_manager import close_redis
from workers.maintenance import run as maintenance_loop
from utils.middleware import AntiFloodMiddleware, AccessMiddleware

logger = logging.getLogger(__name__)


async def on_startup():
    from migrations.runner import migrate
    # Existing installations must gain referenced columns before new FK tables.
    from sqlalchemy import inspect
    async with engine.connect() as conn:
        exists = await conn.run_sync(lambda c: inspect(c).has_table("users"))
    if exists:
        await migrate()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if not exists:
        await migrate()
    from services.payments import seed_products
    await seed_products()
    from services.chats import ChatService
    await ChatService().reconcile()
    logger.info("Database tables created")


async def on_shutdown():
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete")


async def main():
    if not config.BOT_TOKEN:
        print("BOT_TOKEN not set! Create .env file.")
        sys.exit(1)

    from utils.logging import JsonFormatter, UpdateLoggingMiddleware
    stream=logging.StreamHandler()
    stream.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO,handlers=[stream])

    await on_startup()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=RedisStorage.from_url(config.REDIS_URL))

    from handlers import load_routers

    dp.include_routers(*load_routers())

    dp.message.middleware(AntiFloodMiddleware())
    dp.callback_query.middleware(AntiFloodMiddleware())
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.message.outer_middleware(UpdateLoggingMiddleware())
    dp.callback_query.outer_middleware(UpdateLoggingMiddleware())

    # Start background notification loop
    worker = asyncio.create_task(maintenance_loop(bot))

    @dp.errors()
    async def on_error(event):
        import traceback
        logger.error("event=update_failed error_code=%s\n%s", type(event.exception).__name__, "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__)))
        update = event.update
        try:
            if update.callback_query:
                await update.callback_query.answer("Не удалось выполнить действие. Попробуй ещё раз.")
            elif update.message:
                await update.message.answer("Не удалось выполнить действие. Попробуй ещё раз чуть позже.")
        except Exception:
            logger.warning("event=error_reply_failed update_id=%s", update.update_id)
        return True

    logger.info("Bot starting...")

    try:
        from handlers.search import restore_searches
        await restore_searches(bot)
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        from handlers.search import shutdown_searches
        await shutdown_searches()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await dp.storage.close()
        await bot.session.close()
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped")
