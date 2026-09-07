import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from handlers import search_router
from keyboards import texts as T
from keyboards.reply import search_kb_reply, chat_kb_reply, main_menu_kb
from services.matching import MatchingService
from services.chat_manager import get_redis, get_online_count
from services.chats import ChatService
import config

logger = logging.getLogger(__name__)
_tasks = {}
_status_messages = {}
# Compare-and-delete: only the holder that set the token releases the lock, so a
# holder whose TTL already expired can never delete the next holder's lock.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


@asynccontextmanager
async def search_lock(user_id, ttl=10, retry_delay=0.05):
    r = await get_redis()
    key, token = f"search:lock:{user_id}", secrets.token_hex(8)
    while not await r.set(key, token, nx=True, ex=ttl):
        await asyncio.sleep(retry_delay)
    try:
        yield
    finally:
        await r.eval(_RELEASE_SCRIPT, 1, key, token)


async def has_active_participation(user_id):
    from sqlalchemy import select
    from models.base import async_session
    from models.chat import ActiveParticipant
    async with async_session() as s:
        return bool(await s.scalar(select(ActiveParticipant.session_id)
            .where(ActiveParticipant.user_id == user_id)))


async def cancel_task(user_id):
    task = _tasks.pop(user_id, None)
    if task and task is not asyncio.current_task():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def cancel_search(user_id):
    """Cancel the search loop/queue entry and remove the status card.

    Only an explicit user cancel removes the card — a match or timeout leaves
    it as history, since another message already covers that outcome.
    """
    status = _status_messages.pop(user_id, None)
    await cancel_task(user_id)
    await MatchingService().cancel(user_id)
    if status is not None:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass


async def begin_search(message, user_id):
    async with search_lock(user_id):
        await _begin_search(message, user_id)


def search_status_keyboard(offer=None):
    rows = list(offer.inline_keyboard) if offer else []
    return InlineKeyboardMarkup(inline_keyboard=rows + [[
        InlineKeyboardButton(text="Отменить поиск", icon_custom_emoji_id="5273914604752216432", callback_data="search:cancel")]])


async def _begin_search(message, user_id):
    result = await MatchingService().enqueue(user_id)
    task = _tasks.get(user_id)
    if result.code == "ALREADY_SEARCHING" and task and not task.done():
        await message.answer("<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Поиск уже идёт.", reply_markup=search_kb_reply())
        return
    if not result.ok:
        # Send the chat keyboard along with the "press Стоп" answer: a user whose
        # match notice never arrived still has the menu keyboard on screen and no
        # Стоп button to press, which is a dead end.
        markup = chat_kb_reply() if result.code == "ALREADY_CHATTING" else None
        await message.answer({"BANNED": "⛔ Ты заблокирован.", "ALREADY_CHATTING": "Ты уже в диалоге. Нажми «Стоп».",
                              "ONBOARDING_REQUIRED": "Сначала заверши регистрацию: /start"}[result.code],
                             reply_markup=markup)
        return
    await cancel_task(user_id)
    try:
        status = await message.answer(
            f"🔎 Ищу собеседника...\n\n⏱ Прошло: 00:00\n"
            f"👥 Онлайн сейчас: {await get_online_count()}", reply_markup=search_status_keyboard())
    except Exception:
        # Do not leave an orphan queue entry when Telegram rejects the status.
        await MatchingService().cancel(user_id)
        raise
    task = asyncio.create_task(search_loop(status, user_id))
    _tasks[user_id] = task
    _status_messages[user_id] = status


async def search_loop(status, user_id, bot=None):
    bot = bot or status.bot
    try:
        r = await get_redis()
        queued_at = await r.get(f"search:user:{user_id}")
        if queued_at is None:
            await MatchingService().cancel(user_id)
            return
        started = time.monotonic() - max(0, time.time() - float(queued_at))
        # started is reset for each Premium window; queued_since is not,
        # so the cap below measures the whole time spent in the queue.
        queued_since = started
        offer_tracked = False
        status_retry_at = 0.0
        while True:
            r = await get_redis()
            if await r.zscore("search_queue", str(user_id)) is None:
                return
            elapsed = int(time.monotonic()-started)
            from services.settings import bot_value
            if elapsed >= await bot_value("search_timeout", config.SEARCH_TIMEOUT):
                # cancel() takes the matching lock, so any concurrent match has
                # committed by now and is visible below; one that starts after
                # it cannot include this user anymore.
                await MatchingService().cancel(user_id)
                if await has_active_participation(user_id):
                    return
                from utils.db import is_premium
                # Premium keeps its priority window, but no longer searches
                # without end: waiting in the queue past the cap helps nobody.
                cap = await bot_value("premium_search_timeout", config.PREMIUM_SEARCH_TIMEOUT)
                if await is_premium(user_id) and (
                        not cap or time.monotonic()-queued_since < cap):
                    requeued = await MatchingService().enqueue(user_id)
                    if requeued.code == "SEARCH_STARTED":
                        started = time.monotonic()
                        continue
                    return
                await bot.send_message(user_id, "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Пока никого нет. Расширить критерии поиска?", reply_markup=main_menu_kb())
                return
            chat = await MatchingService().find(user_id)
            if chat:
                for uid in (chat.user1_id, chat.user2_id):
                    await cancel_task(uid)
                return
            try:
                from handlers.premium import product_keyboard
                from utils.db import is_premium
                if status is not None and time.monotonic() >= status_retry_at:
                    offer = await product_keyboard(["instant_search"]) if elapsed > await bot_value("search_slow_offer_seconds", 20) and not await is_premium(user_id) else None
                    await status.edit_text(f"<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Ищу{'.' * (elapsed//2%3+1)}\n\n<tg-emoji emoji-id=\"5258258882022612173\">⏱</tg-emoji> Прошло: {elapsed//60:02d}:{elapsed%60:02d}\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Онлайн сейчас: {await get_online_count()}", reply_markup=search_status_keyboard(offer))
                    if offer and not offer_tracked:
                        from services.growth import track
                        await track(user_id,"search_slow_offer_shown",f"slow_search:{user_id}:{queued_at}")
                        offer_tracked=True
            except TelegramRetryAfter as error:
                status_retry_at = time.monotonic() + error.retry_after
            except TelegramBadRequest:
                pass
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("event=search_failed user_id=%s", user_id)
        await MatchingService().cancel(user_id)
        await bot.send_message(user_id, "Поиск временно недоступен. Попробуй ещё раз.", reply_markup=main_menu_kb())
    finally:
        if _tasks.get(user_id) is asyncio.current_task():
            _tasks.pop(user_id, None)
            _status_messages.pop(user_id, None)


@search_router.message(Command("search"))
@search_router.message(F.text == T.SEARCH)
async def cmd_search(message: Message):
    await begin_search(message, message.from_user.id)


@search_router.callback_query(F.data == "search:start")
@search_router.callback_query(F.data == "search:repeat")
async def cb_search_start(callback: CallbackQuery):
    await callback.answer()
    await begin_search(callback.message, callback.from_user.id)


@search_router.message(F.text == T.CANCEL)
async def cmd_cancel(message: Message):
    async with search_lock(message.from_user.id):
        await cancel_search(message.from_user.id)
    await message.answer(T.MENU_TEXT, reply_markup=main_menu_kb())


@search_router.callback_query(F.data == "search:cancel")
async def cb_cancel(callback: CallbackQuery):
    await callback.answer("Поиск отменён")
    async with search_lock(callback.from_user.id):
        await cancel_search(callback.from_user.id)
    await callback.message.answer(T.MENU_TEXT, reply_markup=main_menu_kb())


async def restore_searches(bot):
    r = await get_redis()
    async for value, _ in r.zscan_iter("search_queue"):
        user_id = int(value)
        async with search_lock(user_id):
            task = _tasks.get(user_id)
            if task and not task.done():
                continue
            if not await r.exists(f"search:user:{user_id}"):
                await MatchingService().cancel(user_id)
                continue
            _tasks[user_id] = asyncio.create_task(search_loop(None, user_id, bot))


async def shutdown_searches():
    for uid in list(_tasks):
        await cancel_task(uid)
    _status_messages.clear()
