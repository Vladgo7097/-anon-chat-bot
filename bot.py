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


async def notify_owner_on_startup():
    if not config.OWNER_ID:
        return
    from models.base import async_session
    from models.user import User
    from models.operations import Notification
    from services.notifications import enqueue
    from utils.time import utcnow
    from sqlalchemy import func, select, delete
    now = utcnow()
    # Keep the displayed date and the dedupe window on the same UTC day the
    # rest of the app uses (UTCDateTime columns, utcnow() elsewhere) rather
    # than the host's local timezone, which could disagree near midnight.
    today = now.date().isoformat()
    async with async_session.begin() as s:
        total_users = await s.scalar(select(func.count(User.id))) or 0
        text = (
            f"<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Бот запущен\n\n"
            f"Пользователей: <b>{total_users}</b>\n"
            f"Дата: {today}\n"
            f"Время: {now:%H:%M} UTC"
        )
        dedupe_key = f"startup:{config.OWNER_ID}:{today}"
        # A restart mid-day replaces the day's still-queued notice rather than
        # stacking one per deploy; an already-sent one is not recalled.
        await s.execute(delete(Notification).where(
            Notification.dedupe_key == dedupe_key, Notification.status == "queued"))
        await enqueue(s, config.OWNER_ID, "startup", dedupe_key, text)


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
    await notify_owner_on_startup()


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
    from utils.telegram import RetryTelegramRequests
    bot.session.middleware(RetryTelegramRequests())
    from utils.storage import AvailableRedisStorage
    dp = Dispatcher(storage=AvailableRedisStorage.from_url(config.REDIS_URL))
    from utils.deduplication import UpdateDeduplication
    dp.update.outer_middleware(UpdateDeduplication())

    from handlers import load_routers

    dp.include_routers(*load_routers())

    from utils.albums import AlbumMiddleware
    dp.message.middleware(AlbumMiddleware())
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
