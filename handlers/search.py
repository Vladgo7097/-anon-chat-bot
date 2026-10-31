import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

from aiogram import F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from handlers import search_router
from keyboards import texts as T
from keyboards.reply import search_kb_reply, chat_kb_reply, main_menu_kb
from services.matching import MatchingService
from services.chat_manager import get_redis, get_online_count
from redis.exceptions import RedisError
from utils import bots
import config

logger = logging.getLogger(__name__)

ALREADY_SEARCHING = {
    "ru": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Поиск уже идёт.',
    "en": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Search is already running.',
    "uk": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Пошук вже триває.',
}
FAIL_REASONS = {
    "BANNED": {
        "ru": "⛔ Ты заблокирован.",
        "en": "⛔ You are banned.",
        "uk": "⛔ Тебе заблоковано.",
    },
    "MUTED": {
        "ru": "🔇 Ты в муте — писать и искать собеседника нельзя.",
        "en": "🔇 You're muted — writing and searching are unavailable.",
        "uk": "🔇 Ти в муті — писати і шукати співрозмовника не можна.",
    },
    "ALREADY_CHATTING": {
        "ru": "Ты уже в диалоге. Нажми «Стоп».",
        "en": 'You\'re already chatting. Tap "Stop".',
        "uk": "Ти вже в діалозі. Натисни «Стоп».",
    },
    "ONBOARDING_REQUIRED": {
        "ru": "Сначала заверши регистрацию: /start",
        "en": "Finish registration first: /start",
        "uk": "Спочатку заверши реєстрацію: /start",
    },
}
SEARCHING_STATUS = {
    "ru": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Ищу собеседника...',
    "en": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Looking for a match...',
    "uk": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Шукаю співрозмовника...',
}
SEARCHING_PROGRESS = {
    "ru": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Ищу{dots}',
    "en": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Searching{dots}',
    "uk": '<tg-emoji emoji-id="5976789958407493804">🔎</tg-emoji> Шукаю{dots}',
}
SEARCHING_ONLINE = {
    "ru": '\n\n<tg-emoji emoji-id="5215522595922779944">👥</tg-emoji> Онлайн сейчас: {online}',
    "en": '\n\n<tg-emoji emoji-id="5215522595922779944">👥</tg-emoji> Online now: {online}',
    "uk": '\n\n<tg-emoji emoji-id="5215522595922779944">👥</tg-emoji> Онлайн зараз: {online}',
}
SEARCH_STOPPED = {
    "ru": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Поиск остановлен.',
    "en": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Search stopped.',
    "uk": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Пошук зупинено.',
}
NOBODY_YET = {
    "ru": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Пока никого нет. Расширить критерии поиска?',
    "en": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> No one\'s around yet. Widen your search filters?',
    "uk": '<tg-emoji emoji-id="5296482716567495148">⏳</tg-emoji> Поки що нікого немає. Розширити критерії пошуку?',
}
SEARCH_UNAVAILABLE = {
    "ru": "Поиск временно недоступен. Попробуй ещё раз.",
    "en": "Search is temporarily unavailable. Try again.",
    "uk": "Пошук тимчасово недоступний. Спробуй ще раз.",
}
CANCEL_LABEL = {"ru": "Отменить поиск", "en": "Cancel search", "uk": "Скасувати пошук"}
SEARCH_CANCELLED_TOAST = {
    "ru": "Поиск отменён",
    "en": "Search cancelled",
    "uk": "Пошук скасовано",
}
WAITING_SAVED = {
    "ru": "🔔 Хорошо, сообщим в течение 12 часов, когда кто-то начнёт поиск.",
    "en": "🔔 We’ll notify you within 12 hours when someone starts searching.",
    "uk": "🔔 Повідомимо протягом 12 годин, коли хтось почне пошук.",
}
WAITING_AVAILABLE = {
    "ru": "🔔 Кто-то появился в поиске. Можно попробовать ещё раз!",
    "en": "🔔 Someone is searching now. You can try again!",
    "uk": "🔔 Хтось уже шукає співрозмовника. Спробуй ще раз!",
}

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
        return bool(
            await s.scalar(
                select(ActiveParticipant.session_id).where(
                    ActiveParticipant.user_id == user_id
                )
            )
        )


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
    it as history, since another message already covers that outcome. After a
    restart the live object is gone, so the Redis reference takes over.
    """
    status = _status_messages.pop(user_id, None)
    await cancel_task(user_id)
    await MatchingService().cancel(user_id)
    await _clear_search_card(user_id, status=status)


# Redis-сбои, которые ловим на входе поиска/отмены: до создания карточки или
# записи очереди они чистые — честный "временно недоступен" лучше обезличенной
# ошибки из глобального ловца. Остальные Redis-точки внутри уже best effort.
_REDIS_DOWN = (RedisError, TimeoutError, ConnectionError)


def card_key(user_id):
    return f"search:card:{user_id}"


async def _save_search_card(user_id, status):
    """Карточка переживает рестарт только через Redis-ссылку с chat_id и
    message_id — живой объект между процессами не переезжает. TTL покрывает
    максимум жизни очереди: окно + premium-потолок + запас."""
    try:
        from services.settings import bot_value

        timeout = await bot_value("search_timeout", config.SEARCH_TIMEOUT)
        cap = await bot_value("premium_search_timeout", config.PREMIUM_SEARCH_TIMEOUT)
        r = await get_redis()
        await r.set(
            card_key(user_id),
            f"{status.chat.id}:{status.message_id}",
            ex=int(timeout) + max(0, int(cap)) + 120,
        )
    except Exception:
        # best effort: сбой ссылки никогда не должен уронить уже стартовавший
        # поиск — без неё рестарт просто не найдёт старую карточку.
        logger.warning(
            "event=search_card_save_failed user_id=%s", user_id, exc_info=True
        )


def _parse_card_ref(ref):
    # Мусор в ключе не должен ломать отмену: непонятная ссылка = нет карточки.
    chat_id, _, message_id = str(ref).partition(":")
    if chat_id.isdigit() and message_id.isdigit():
        return int(chat_id), int(message_id)
    return None


async def _clear_search_card(user_id, status=None):
    """Убрать ссылку и саму карточку, если она ещё жива.

    Живой объект важнее ссылки: он пришёл с живого события того же бота.
    После рестарта остаётся только ссылка — по ней карточку удаляет бот,
    с которым юзер реально разговаривает.
    """
    ref = None
    try:
        r = await get_redis()
        ref = await r.get(card_key(user_id))
        if ref is not None:
            await r.delete(card_key(user_id))
    except _REDIS_DOWN:
        return
    if status is not None:
        try:
            await status.delete()
        except TelegramBadRequest:
            pass
        return
    ids = None if ref is None else _parse_card_ref(ref)
    if ids is None:
        return
    try:
        bot = await bots.bot_for(user_id)
        await bot.delete_message(*ids)
    except Exception:
        logger.exception("event=search_card_cleanup_failed user_id=%s", user_id)


class _RestoredCard:
    """Карточка, пережившая рестарт: edit/delete уходят в Telegram по
    сохранённым chat_id и message_id, не через мёртвый объект."""

    def __init__(self, bot, chat_id, message_id):
        self.bot = bot
        self.chat = SimpleNamespace(id=chat_id)
        self.message_id = message_id

    async def edit_text(self, text, reply_markup=None, **kwargs):
        return await self.bot.edit_message_text(
            text,
            chat_id=self.chat.id,
            message_id=self.message_id,
            reply_markup=reply_markup,
            **kwargs,
        )

    async def delete(self):
        return await self.bot.delete_message(self.chat.id, self.message_id)


async def _restored_status(r, user_id):
    """Живой объект карточки после рестарта или None, если её нет.

    Возвращённая карточка снова анимируется точками и переиздаётся циклом
    поиска; на отмену она удаляется, как будто бот не перевоплощался.
    """
    try:
        ref = await r.get(card_key(user_id))
    except _REDIS_DOWN:
        return None
    ids = _parse_card_ref(ref) if ref else None
    if ids is None:
        return None
    try:
        return _RestoredCard(await bots.bot_for(user_id), *ids)
    except Exception:
        logger.exception("event=restore_card_failed user_id=%s", user_id)
        return None


async def begin_search(message, user_id, lang="ru"):
    """Вход поиска с мягким ответом на недоступный Redis.

    До создания записи очереди сбой Redis чистый: ничего не поставлено в
    очередь, SEARCH_UNAVAILABLE — честный ответ. Поздние сбои разбирают
    _begin_search и search_loop, не эта обёртка.
    """
    try:
        async with search_lock(user_id):
            await _begin_search(message, user_id, lang)
    except _REDIS_DOWN:
        logger.warning("event=search_redis_down user_id=%s", user_id)
        await message.answer(
            T.tr(SEARCH_UNAVAILABLE, lang), reply_markup=main_menu_kb(lang)
        )


def search_status_keyboard(offer=None, lang="ru"):
    rows = list(offer.inline_keyboard) if offer else []
    return InlineKeyboardMarkup(
        inline_keyboard=rows
        + [
            [
                InlineKeyboardButton(
                    text=T.tr(CANCEL_LABEL, lang),
                    icon_custom_emoji_id="5273914604752216432",
                    callback_data="search:cancel",
                )
            ]
        ]
    )


def search_status_text(template, lang="ru", dots="", online=None):
    """Текст карточки поиска; онлайн-счётчик видит только владелец.

    online=None убирает строку целиком, у пользователей счётчика нет.
    """
    text = T.tr(template, lang).format(dots=dots)
    if online is not None:
        text += T.tr(SEARCHING_ONLINE, lang).format(online=online)
    return text


def nobody_keyboard(lang="ru"):
    # The referral button lives in the persistent main menu only. This screen
    # keeps the contextual "notify me" action; duplicating the invite here
    # made the same CTA appear in two unrelated places.
    labels = {
        "ru": "🔔 Сообщить, когда кто-то появится",
        "en": "🔔 Notify me when someone appears",
        "uk": "🔔 Повідомити, коли хтось з’явиться",
    }
    wait = labels.get(lang, labels["ru"])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=wait, callback_data="search:wait"),
            ]
        ]
    )


async def notify_waiting_users(bot, newcomer_id):
    """Wake opted-in users once a fresh potential partner enters the queue."""
    r = await get_redis()
    ids = await r.smembers("search:waiting_alerts")
    if not ids:
        return
    from repositories.users import UserRepository

    for raw_id in ids:
        user_id = int(raw_id)
        if user_id == newcomer_id or not await r.exists(
            f"search:waiting_alert:{user_id}"
        ):
            await r.srem("search:waiting_alerts", raw_id)
            continue
        await r.delete(f"search:waiting_alert:{user_id}")
        await r.srem("search:waiting_alerts", raw_id)
        row = await UserRepository().get(user_id)
        lang = getattr(row, "language", None) or "ru"
        # The queue spans both bots, so each waiter is woken through the bot
        # they actually talk to -- the other token would just get a 403.
        target = bots.resolve(getattr(row, "bot_id", None)) or bot
        try:
            await target.send_message(
                user_id,
                T.tr(WAITING_AVAILABLE, lang),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=T.label(T.SEARCH, lang),
                                callback_data="search:repeat",
                                style="primary",
                            )
                        ]
                    ]
                ),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            continue


async def _begin_search(message, user_id, lang="ru"):
    result = await MatchingService().enqueue(user_id)
    task = _tasks.get(user_id)
    if result.code == "ALREADY_SEARCHING" and task and not task.done():
        await message.answer(
            T.tr(ALREADY_SEARCHING, lang), reply_markup=search_kb_reply(lang)
        )
        return
    if not result.ok:
        if result.code == "MUTED":
            from services.onboarding import mute_alert

            await message.answer(mute_alert((result.data or {}).get("until"), lang))
            return
        # Send the chat keyboard along with the "press Стоп" answer: a user whose
        # match notice never arrived still has the menu keyboard on screen and no
        # Стоп button to press, which is a dead end.
        markup = chat_kb_reply(lang) if result.code == "ALREADY_CHATTING" else None
        await message.answer(T.tr(FAIL_REASONS[result.code], lang), reply_markup=markup)
        return
    # Пробуждение ждущих — best effort: сбой Redis не должен превращать
    # уже созданную запись очереди в осиротевшую.
    try:
        await notify_waiting_users(message.bot, user_id)
    except _REDIS_DOWN:
        logger.warning("event=waiting_notify_skipped user_id=%s", user_id)
    await cancel_task(user_id)
    # Старая карточка с прошлых заплаток (рестарт, прерванный цикл) снимается
    # до выпуска новой — иначе юзер копит её как снежный ком.
    await _clear_search_card(user_id)
    # Живой счётчик онлайна в карточке — только для владельца, остальным он
    # без пользы и лишний раз дёргает Redis в поиске. user_id — авторитетный
    # источник: на callback-пути message.from_user это сам бот.
    try:
        online = await get_online_count() if user_id == config.OWNER_ID else None
    except _REDIS_DOWN:
        online = None
    try:
        status = await message.answer(
            search_status_text(SEARCHING_STATUS, lang, online=online),
            reply_markup=search_status_keyboard(lang=lang),
        )
    except Exception:
        # Do not leave an orphan queue entry when Telegram rejects the status.
        await MatchingService().cancel(user_id)
        raise
    task = asyncio.create_task(search_loop(status, user_id, lang=lang))
    _tasks[user_id] = task
    _status_messages[user_id] = status
    await _save_search_card(user_id, status)


async def search_loop(status, user_id, bot=None, lang="ru"):
    if bot is None:
        bot = status.bot if status is not None else None
    if bot is None:
        # A search restored after a restart carries no live event to read a bot
        # from, and either side of a match may have joined through the other
        # bot, so the sender is resolved from the row rather than inherited.
        bot = await bots.bot_for(user_id)
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
                # Same wrap-up as the timeout below: cancel() takes the
                # matching lock first, so an in-flight match has committed
                # and is visible in the check that follows. Without this the
                # silent exit left the card animating "searching" forever
                # after another searcher's stale scan dropped the entry.
                await MatchingService().cancel(user_id)
                if await has_active_participation(user_id):
                    return
                try:
                    await bot.send_message(
                        user_id,
                        T.tr(NOBODY_YET, lang),
                        reply_markup=nobody_keyboard(lang),
                    )
                except (TelegramForbiddenError, TelegramBadRequest):
                    pass
                return
            elapsed = int(time.monotonic() - started)
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
                cap = await bot_value(
                    "premium_search_timeout", config.PREMIUM_SEARCH_TIMEOUT
                )
                if await is_premium(user_id) and (
                    not cap or time.monotonic() - queued_since < cap
                ):
                    # One press = one search_started: requeues reuse the
                    # session's first queued_at instead of minting a fresh
                    # event for every 180 seconds of waiting.
                    requeued = await MatchingService().enqueue(
                        user_id, event_key=f"search:{user_id}:{float(queued_at)}"
                    )
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
                        await bot.send_message(
                            user_id,
                            T.tr(SEARCH_STOPPED, lang),
                            reply_markup=main_menu_kb(lang),
                        )
                    except (TelegramForbiddenError, TelegramBadRequest):
                        pass
                    return
                try:
                    await bot.send_message(
                        user_id,
                        T.tr(NOBODY_YET, lang),
                        reply_markup=nobody_keyboard(lang),
                    )
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
                    offer = (
                        await product_keyboard(["instant_search"])
                        if elapsed > await bot_value("search_slow_offer_seconds", 20)
                        and not await is_premium(user_id)
                        else None
                    )
                    dots = "." * (elapsed // 2 % 3 + 1)
                    await status.edit_text(
                        search_status_text(
                            SEARCHING_PROGRESS,
                            lang,
                            dots=dots,
                            online=(
                                await get_online_count()
                                if user_id == config.OWNER_ID
                                else None
                            ),
                        ),
                        reply_markup=search_status_keyboard(offer, lang),
                    )
                    if offer and not offer_tracked:
                        from services.growth import track

                        await track(
                            user_id,
                            "search_slow_offer_shown",
                            f"slow_search:{user_id}:{queued_at}",
                        )
                        offer_tracked = True
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
            await bot.send_message(
                user_id, T.tr(SEARCH_UNAVAILABLE, lang), reply_markup=main_menu_kb(lang)
            )
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
    try:
        async with search_lock(message.from_user.id):
            await cancel_search(message.from_user.id)
    except _REDIS_DOWN:
        await message.answer(
            T.tr(SEARCH_UNAVAILABLE, lang), reply_markup=main_menu_kb(lang)
        )
        return
    await message.answer(T.menu_text(lang), reply_markup=main_menu_kb(lang))


@search_router.callback_query(F.data == "search:cancel")
async def cb_cancel(callback: CallbackQuery, lang: str = "ru"):
    try:
        async with search_lock(callback.from_user.id):
            await cancel_search(callback.from_user.id)
    except _REDIS_DOWN:
        await callback.answer(T.tr(SEARCH_UNAVAILABLE, lang), show_alert=True)
        return
    await callback.answer(T.tr(SEARCH_CANCELLED_TOAST, lang))
    await callback.message.answer(T.menu_text(lang), reply_markup=main_menu_kb(lang))


@search_router.callback_query(F.data == "search:wait")
async def cb_wait_for_match(callback: CallbackQuery, lang: str = "ru"):
    try:
        r = await get_redis()
        await r.set(f"search:waiting_alert:{callback.from_user.id}", "1", ex=12 * 3600)
        await r.sadd("search:waiting_alerts", str(callback.from_user.id))
        await r.expire("search:waiting_alerts", 12 * 3600)
    except _REDIS_DOWN:
        await callback.answer(T.tr(SEARCH_UNAVAILABLE, lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(T.tr(WAITING_SAVED, lang))


NOT_ONBOARDED = {
    "ru": "Сначала заверши регистрацию.",
    "en": "Finish registration first.",
    "uk": "Спочатку заверши реєстрацію.",
}
INVITE_MESSAGE = {
    "ru": "Пригласи друга — так шанс найти собеседника станет выше:\n{link}",
    "en": "Invite a friend — that raises the odds of finding a match:\n{link}",
    "uk": "Запроси друга — так шанс знайти співрозмовника стане вищим:\n{link}",
}


@search_router.callback_query(F.data == "search:invite")
async def cb_invite_friend(callback: CallbackQuery, lang: str = "ru"):
    from models.base import async_session
    from models.user import User
    from sqlalchemy import select

    async with async_session() as s:
        code = await s.scalar(
            select(User.referral_code).where(User.telegram_id == callback.from_user.id)
        )
    if not code:
        await callback.answer(T.tr(NOT_ONBOARDED, lang), show_alert=True)
        return
    await callback.answer()
    bot = await callback.bot.get_me()
    link = f"https://t.me/{bot.username}?start=ref_{code}"
    await callback.message.answer(T.tr(INVITE_MESSAGE, lang).format(link=link))


async def restore_searches():
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
            # No live event to read a bot or a language from. search_loop
            # resolves the sender per user from the row, which is the only way
            # to reach a search that was started on the other bot; the same
            # row supplies the language, so a uk-user's timeout post-restart
            # does not arrive in Russian.
            lang = "ru"
            from repositories.users import UserRepository

            row = await UserRepository().get(user_id)
            if row is not None:
                lang = getattr(row, "language", None) or "ru"
            status = await _restored_status(r, user_id)
            _tasks[user_id] = asyncio.create_task(
                search_loop(status, user_id, lang=lang)
            )
            if status is not None:
                _status_messages[user_id] = status


async def shutdown_searches():
    for uid in list(_tasks):
        await cancel_task(uid)
    _status_messages.clear()
