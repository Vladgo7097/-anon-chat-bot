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

ALREADY_SEARCHING = {"ru": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Поиск уже идёт.",
                      "en": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Search is already running.",
                      "uk": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Пошук вже триває."}
FAIL_REASONS = {
    "BANNED": {"ru": "⛔ Ты заблокирован.", "en": "⛔ You are banned.", "uk": "⛔ Тебе заблоковано."},
    "ALREADY_CHATTING": {"ru": "Ты уже в диалоге. Нажми «Стоп».", "en": "You're already chatting. Tap \"Stop\".",
                          "uk": "Ти вже в діалозі. Натисни «Стоп»."},
    "ONBOARDING_REQUIRED": {"ru": "Сначала заверши регистрацию: /start", "en": "Finish registration first: /start",
                             "uk": "Спочатку заверши реєстрацію: /start"},
}
SEARCHING_STATUS = {"ru": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Ищу собеседника...\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Онлайн сейчас: {online}",
                     "en": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Looking for a match...\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Online now: {online}",
                     "uk": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Шукаю співрозмовника...\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Онлайн зараз: {online}"}
SEARCHING_PROGRESS = {"ru": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Ищу{dots}\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Онлайн сейчас: {online}",
                       "en": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Searching{dots}\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Online now: {online}",
                       "uk": "<tg-emoji emoji-id=\"5976789958407493804\">🔎</tg-emoji> Шукаю{dots}\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Онлайн зараз: {online}"}
SEARCH_STOPPED = {"ru": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Поиск остановлен.",
                   "en": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Search stopped.",
                   "uk": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Пошук зупинено."}
NOBODY_YET = {"ru": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Пока никого нет. Расширить критерии поиска?",
              "en": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> No one's around yet. Widen your search filters?",
              "uk": "<tg-emoji emoji-id=\"5296482716567495148\">⏳</tg-emoji> Поки що нікого немає. Розширити критерії пошуку?"}
SEARCH_UNAVAILABLE = {"ru": "Поиск временно недоступен. Попробуй ещё раз.",
                       "en": "Search is temporarily unavailable. Try again.",
                       "uk": "Пошук тимчасово недоступний. Спробуй ще раз."}
CANCEL_LABEL = {"ru": "Отменить поиск", "en": "Cancel search", "uk": "Скасувати пошук"}
SEARCH_CANCELLED_TOAST = {"ru": "Поиск отменён", "en": "Search cancelled", "uk": "Пошук скасовано"}

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
    # Whoever removes the task entry also owns clearing the status message
    # reference: search_loop's own `finally` can no longer do it once its
    # _tasks entry has already been popped here (e.g. on a match, where both
    # participants' tasks are cancelled from the task that found the match).
    _status_messages.pop(user_id, None)
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


async def begin_search(message, user_id, lang="ru"):
    async with search_lock(user_id):
        await _begin_search(message, user_id, lang)


def search_status_keyboard(offer=None, lang="ru"):
    rows = list(offer.inline_keyboard) if offer else []
    return InlineKeyboardMarkup(inline_keyboard=rows + [[
        InlineKeyboardButton(text=T.tr(CANCEL_LABEL, lang), icon_custom_emoji_id="5273914604752216432", callback_data="search:cancel")]])


async def _begin_search(message, user_id, lang="ru"):
    result = await MatchingService().enqueue(user_id)
    task = _tasks.get(user_id)
    if result.code == "ALREADY_SEARCHING" and task and not task.done():
        await message.answer(T.tr(ALREADY_SEARCHING, lang), reply_markup=search_kb_reply(lang))
        return
    if not result.ok:
        # Send the chat keyboard along with the "press Стоп" answer: a user whose
        # match notice never arrived still has the menu keyboard on screen and no
        # Стоп button to press, which is a dead end.
        markup = chat_kb_reply(lang) if result.code == "ALREADY_CHATTING" else None
        await message.answer(T.tr(FAIL_REASONS[result.code], lang), reply_markup=markup)
        return
    await cancel_task(user_id)
    try:
        status = await message.answer(
            T.tr(SEARCHING_STATUS, lang).format(online=await get_online_count()),
            reply_markup=search_status_keyboard(lang=lang))
    except Exception:
        # Do not leave an orphan queue entry when Telegram rejects the status.
        await MatchingService().cancel(user_id)
        raise
    task = asyncio.create_task(search_loop(status, user_id, lang=lang))
    _tasks[user_id] = task
    _status_messages[user_id] = status


async def search_loop(status, user_id, bot=None, lang="ru"):
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
                    if requeued.code == "ALREADY_CHATTING":
                        # A concurrent match already covered this: the other
                        # side's search_loop sent the match notice and called
                        # cancel_task for both of us. Saying anything here
                        # would just contradict that message.
                        return
                    try:
                        await bot.send_message(user_id, T.tr(SEARCH_STOPPED, lang), reply_markup=main_menu_kb(lang))
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    return
                try:
                    await bot.send_message(user_id, T.tr(NOBODY_YET, lang), reply_markup=main_menu_kb(lang))
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass
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
                    dots = "." * (elapsed//2%3+1)
                    await status.edit_text(T.tr(SEARCHING_PROGRESS, lang).format(dots=dots, online=await get_online_count()), reply_markup=search_status_keyboard(offer, lang))
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
        try:
            await bot.send_message(user_id, T.tr(SEARCH_UNAVAILABLE, lang), reply_markup=main_menu_kb(lang))
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
    finally:
        if _tasks.get(user_id) is asyncio.current_task():
            _tasks.pop(user_id, None)
            _status_messages.pop(user_id, None)


@search_router.message(Command("search"))
@search_router.message(F.text.in_(T.all_labels(T.SEARCH)))
async def cmd_search(message: Message, lang: str = "ru"):
    await begin_search(message, message.from_user.id, lang)


@search_router.callback_query(F.data == "search:start")
@search_router.callback_query(F.data == "search:repeat")
async def cb_search_start(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await begin_search(callback.message, callback.from_user.id, lang)


@search_router.message(F.text.in_(T.all_labels(T.CANCEL)))
async def cmd_cancel(message: Message, lang: str = "ru"):
    async with search_lock(message.from_user.id):
        await cancel_search(message.from_user.id)
    await message.answer(T.menu_text(lang), reply_markup=main_menu_kb(lang))


@search_router.callback_query(F.data == "search:cancel")
async def cb_cancel(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer(T.tr(SEARCH_CANCELLED_TOAST, lang))
    async with search_lock(callback.from_user.id):
        await cancel_search(callback.from_user.id)
    await callback.message.answer(T.menu_text(lang), reply_markup=main_menu_kb(lang))


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
            # Bot-restart recovery only: no live event to read a language from,
            # and this is rare/short-lived enough that defaulting to Russian
            # here (rather than a real DB round-trip) is an acceptable trade.
            _tasks[user_id] = asyncio.create_task(search_loop(None, user_id, bot))


async def shutdown_searches():
    for uid in list(_tasks):
        await cancel_task(uid)
    _status_messages.clear()
