"""Two bots run out of one process against one database.

Anything sent outside the dispatcher -- the worker's notices, a search timer
fired after a restart -- holds no live update to answer with, so it has to
leave through the bot that recipient actually talks to. The other token gets
a 403 and the message is simply lost.

A user's own bot lives on User.bot_id, written by AccessMiddleware whenever
it differs. register() runs before the first send, so primary() is never
empty by the time anything can reach it.
"""
import logging

from aiogram import Bot

from locales import DEFAULT_LANG, normalize

logger = logging.getLogger(__name__)

_bot_by_id: dict[int, Bot] = {}
_primary: Bot | None = None


def register(bot: Bot, *, primary: bool = False) -> None:
    global _primary
    _bot_by_id[bot.id] = bot
    if primary:
        _primary = bot


def all() -> tuple[Bot, ...]:
    """Every bot this process polls, for checks that are per-process."""
    return tuple(_bot_by_id.values())


def resolve(bot_id) -> Bot | None:
    """Synchronous lookup for callers that already hold the row."""
    return _bot_by_id.get(bot_id)


def primary() -> Bot:
    if _primary is not None:
        return _primary
    if _bot_by_id:
        return next(iter(_bot_by_id.values()))
    msg = "utils.bots: register() was never called"
    raise RuntimeError(msg)


def default_lang(bot, actor=None) -> str:
    """Language for a user who has never picked one.

    The primary bot follows the client's own language_code -- Telegram already
    knows what its user reads. The second bot is Ukrainian by default so the
    audience it is aimed at lands there without a tap; Settings still offers
    the picker afterwards.
    """
    if actor is not None and getattr(bot, "detect_client_lang", True):
        return normalize(getattr(actor, "language_code", None))
    return normalize(getattr(bot, "default_lang", DEFAULT_LANG))


async def bot_for(user_id, fallback: Bot | None = None) -> Bot:
    """The bot this user actually talks to.

    `fallback` is what the caller was already holding, so a lookup that cannot
    run (no row yet, database hiccup) degrades to the old single-bot behaviour
    instead of dropping the message on the floor.
    """
    try:
        from repositories.users import UserRepository
        row = await UserRepository().get(user_id)
        bot = resolve(getattr(row, "bot_id", None))
        if bot is not None:
            return bot
    except Exception:
        logger.debug("event=bot_route_unavailable user_id=%s", user_id)
    return fallback or primary()
