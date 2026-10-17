import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from models.base import engine, Base
from services.chat_manager import close_redis
from workers.maintenance import run as maintenance_loop
from utils.middleware import AntiFloodMiddleware, AccessMiddleware
from utils import bot_health

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

    def build_bot(token, default_lang, detect_client_lang):
        from utils.telegram import RetryTelegramRequests, TimeoutBoundedSession
        # Канал до Telegram рвёт соединения посреди TLS: короткий потолок
        # превращает зависание в быструю неудачу, а middleware -- в повтор.
        instance = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=TimeoutBoundedSession(),
        )
        instance.session.middleware(RetryTelegramRequests())
        # Bot.id is read out of the token, so telling the two apart costs no
        # API call -- and these two attributes are what utils.bots.default_lang
        # and the /start language auto-advance read to pick a fallback.
        instance.default_lang = default_lang
        instance.detect_client_lang = detect_client_lang
        return instance

    from locales import DEFAULT_LANG
    from utils import bots as bot_registry
    instances = [build_bot(config.BOT_TOKEN, DEFAULT_LANG, True)]
    if config.BOT2_TOKEN:
        # Same database, same handlers, same user pool: a user of either bot can
        # be matched with a user of the other, and every relay already goes
        # through whichever bot delivered the message.
        instances.append(build_bot(config.BOT2_TOKEN, config.BOT2_DEFAULT_LANG, False))
    for index, instance in enumerate(instances):
        bot_registry.register(instance, primary=index == 0)
    # Метки живости -- единственное, по чему healthcheck отличает работающий
    # опрос от зависшего. Без этой строки контейнер был бы здоров ровно до
    # первой минуты работы, пока воркер не прогонит probe() сам.
    await bot_health.probe(force=True)

    from utils.storage import AvailableRedisStorage
    # Same reasoning as services/chat_manager.get_redis(): without a socket
    # timeout, a stalled Redis connection blocks FSM reads/writes forever
    # rather than raising the RedisError this storage already falls back on.
    dp = Dispatcher(storage=AvailableRedisStorage.from_url(config.REDIS_URL, connection_kwargs={
        "socket_connect_timeout": 3, "socket_timeout": 5, "health_check_interval": 30}))
    from utils.deduplication import UpdateDeduplication
    dp.update.outer_middleware(UpdateDeduplication())
    # A dead token and a token someone else is polling both look identical
    # from outside: the bot simply goes quiet, and aiogram keeps retrying
    # forever without telling anybody. The polling log is the only place the
    # reason ever shows up, so this is where it gets caught.
    bot_health.attach(dp)

    from handlers import load_routers

    dp.include_routers(*load_routers())

    from utils.albums import AlbumMiddleware
    dp.message.middleware(AlbumMiddleware())
    # AntiFlood -- outer и до Access: aiogram вызывает outer в порядке
    # регистрации, и раньше Access означает лимит ДО его DB-работы
    # (get_or_create/set_online/streak). Как inner-миддлвар он не вызывался
    # вовсе для незарегистрированных -- Access возвращал раньше.
    dp.message.outer_middleware(AntiFloodMiddleware())
    dp.callback_query.outer_middleware(AntiFloodMiddleware())
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.message.outer_middleware(UpdateLoggingMiddleware())
    dp.callback_query.outer_middleware(UpdateLoggingMiddleware())

    # Start background notification loop. It routes every notice through
    # utils.bots.bot_for(), so this bot is only the fallback.
    worker = asyncio.create_task(maintenance_loop(instances[0]))

    @dp.errors()
    async def on_error(event, bot=None):
        import traceback
        logger.error("event=update_failed error_code=%s\n%s", type(event.exception).__name__, "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__)))
        update = event.update
        # This trap runs with no middleware data, so it resolves the language
        # itself -- the DB row first, the delivering bot's own default.
        from keyboards import texts as T
        from utils import bots
        from utils.db import user_lang
        actor = getattr(update.callback_query or update.message, "from_user", None)
        fallback = bots.default_lang(bot, actor)
        try:
            lang = await user_lang(getattr(actor, "id", None), fallback)
        except Exception:
            lang = fallback
        try:
            if update.callback_query:
                await update.callback_query.answer(T.tr(T.ERRORS, lang)["retry"])
            elif update.message:
                await update.message.answer(T.tr(T.ERRORS, lang)["retry_later"])
        except Exception:
            logger.warning("event=error_reply_failed update_id=%s", update.update_id)
        return True

    logger.info("Bot starting...")

    try:
        from handlers.search import restore_searches
        await restore_searches()
        # One dispatcher, one polling loop per bot: aiogram builds a fresh
        # workflow_data per bot, the dedupe key carries bot.id, and the FSM
        # StorageKey carries it too, so the two never collide on shared state.
        await dp.start_polling(*instances)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        from handlers.search import shutdown_searches
        await shutdown_searches()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await dp.storage.close()
        await asyncio.gather(*(instance.session.close() for instance in instances))
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped")
