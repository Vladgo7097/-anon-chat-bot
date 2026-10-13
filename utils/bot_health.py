"""Отказы, о которых владелец обязан узнать сам, а не из жалобы.

Telegram не присылает событий, когда бот перестал работать. aiogram ещё и
зацикливает любую ошибку внутри _listen_updates, то есть мёртвый токен или
опрашивающий тот же токен чужой софт выглядят наружу ровно одинаково: бот
молчит, воронка замирает, и никто не узнаёт неделями.

Оба отказа, ради которых это написано:

* `conflict` — тот же токен дёргает кто-то ещё. Входящие уходят к нему,
  пользователи разговаривают с чужим софтом, а наши ответы доходят только
  когда вытягиваем апдейты мы.
* `unauthorized` — токен мёртв: отозван в BotFather, переназначен, опечатан.

Классификация идёт по двум независимым каналам, потому что каждый ломается
своим способом. Ловля лога точна, но держится на формате сообщения aiogram.
Периодический get_me — тупой, но формат не ломается никогда.
"""
import logging
import re
import time
from collections import deque

from aiogram import Bot

from utils import bots

logger = logging.getLogger(__name__)

# Ошибка в пятую минуту подряд — это уже не сетевой сбой.
WINDOW = 300
THRESHOLD = 5
# Одно уведомление на инцидент, а не на каждую повторную попытку: живущий
# час конфликт иначе зальёт чат владельца сотней копий одного и того же.
COOLDOWN = 1800
PROBE_INTERVAL = 60

HUMAN = {
    "conflict": "этот же токен опрашивает кто-то ещё — входящие уходят к нему",
    "unauthorized": "токен не принимается Telegram — бот мёртв",
}
KIND_BY_CLASS = {
    "TelegramConflictError": "conflict",
    "TelegramUnauthorizedError": "unauthorized",
}

_hits: dict[tuple[int, str], deque] = {}
_paged: dict[tuple[int, str], float] = {}
_pending_kind: str | None = None
_last_probe = 0.0


def record(bot_id, kind, now=None):
    """Запомнить отказ. kind None — транзиентный сбой, его никто не пингует."""
    if kind is None or bot_id is None:
        return
    hits = _hits.setdefault((int(bot_id), kind), deque())
    now = time.monotonic() if now is None else now
    hits.append(now)
    _trim(hits, now)


def _trim(hits, now):
    while hits and now - hits[0] > WINDOW:
        hits.popleft()


def drain(now=None):
    """Инциденты, о которых стоит сообщить владельцу прямо сейчас.

    Каждый возвращается один раз на COOLDOWN: воркер зовёт это на каждом
    тике, а сообщить нужно ровно столько, чтобы человек заметил, и не больше.
    """
    now = time.monotonic() if now is None else now
    ready = []
    for (bot_id, kind), hits in list(_hits.items()):
        _trim(hits, now)
        if len(hits) < THRESHOLD:
            continue
        if now - _paged.get((bot_id, kind), float("-inf")) < COOLDOWN:
            continue
        _paged[(bot_id, kind)] = now
        ready.append((bot_id, kind, len(hits)))
    return ready


def notice_text(bot_id, kind, count):
    return (
        "⚠️ Бот перестал работать\n\n"
        f"Бот ID: {bot_id}\n"
        f"Причина: {HUMAN.get(kind, kind)}\n"
        f"Ошибок за последние {WINDOW // 60} мин: {count}\n\n"
        "Пока токен не выправлен, этим ботом никто не пользуется. "
        "Лечится в @BotFather."
    )


async def report():
    """Уложить инциденты в очередь владельцу. Возвращает, сколько ушло."""
    import config
    if not config.OWNER_ID:
        return 0
    incidents = drain()
    if not incidents:
        return 0
    from models.base import async_session
    from services.notifications import enqueue
    async with async_session.begin() as s:
        for bot_id, kind, count in incidents:
            # Ключ обязан меняться от уведомления к уведомлению: dedupe_key
            # уникален, и постоянный ключ заглушил бы владельца навсегда после
            # самой первой страницы. Окно в имени -- это и есть COOLDOWN.
            bucket = int(time.time() // COOLDOWN)
            await enqueue(s, config.OWNER_ID, "admin_message",
                          f"bot_health:{bot_id}:{kind}:{bucket}",
                          notice_text(bot_id, kind, count))
    logger.warning("event=bot_health_page sent=%s kinds=%s",
                   len(incidents), [kind for _, kind, _ in incidents])
    return len(incidents)


class DispatcherWatcher(logging.Handler):
    """Превращает молчание поллинга в запись, которую можно показать человеку.

    aiogram печатает класс ошибки и bot id двумя соседними строками и без
    пары: первая — "Failed to fetch updates - TelegramConflictError: ...",
    вторая — "... (tryings = 16, bot id = 7828301604)". Класс помнится до
    прихода bot id, дальше склеивается. Если формат в какой-то версии
    изменится, просто ничего не запишется — но канал get_me ниже останется.
    """

    _PENDING = re.compile(r"Failed to fetch updates - (\w+)")
    _BOT_ID = re.compile(r"bot id = (\d+)")

    def emit(self, log_record):
        global _pending_kind
        try:
            message = log_record.getMessage()
        except Exception:
            return
        if "Failed to fetch updates" in message:
            match = self._PENDING.search(message)
            if match:
                _pending_kind = KIND_BY_CLASS.get(match.group(1))
            return
        if _pending_kind is None:
            return
        match = self._BOT_ID.search(message)
        if match:
            # Параметр не может называться record(): он затёр бы одноимённую
            # функцию модуля, и ловушка падала бы на каждой строке лога.
            record(int(match.group(1)), _pending_kind)
            _pending_kind = None


def attach(dp):
    """Повесить ловушку на лог диспетчера — там aiogram печатает отказы."""
    handler = DispatcherWatcher()
    handler.setLevel(logging.WARNING)
    logging.getLogger("aiogram.dispatcher").addHandler(handler)


async def probe(force: bool = False):
    """Раз в PROBE_INTERVAL спросить Telegram напрямую, жив ли каждый токен.

    Тупой канал против хрупкого лога: get_me не меняет формат сообщений и не
    зависит от того, что именно aiogram решил написать в конкретной версии.
    """
    global _last_probe
    now = time.monotonic()
    if not force and now - _last_probe < PROBE_INTERVAL:
        return
    _last_probe = now
    for bot in bots.all():
        try:
            await bot.get_me()
        except Exception as error:
            record(bot.id, classify(error))
        else:
            # Токен живой — сбрасываем счётчик: иначе старый конфликт
            # повторно выстрелит через COOLDOWN и заспамит уведомлениями.
            _hits.pop((bot.id, "unauthorized"), None)


def classify(error):
    """Что именно сломалось, если об этом обязан узнать человек."""
    from aiogram.exceptions import (TelegramConflictError,
                                    TelegramUnauthorizedError)
    if isinstance(error, TelegramConflictError):
        return "conflict"
    if isinstance(error, TelegramUnauthorizedError):
        return "unauthorized"
    return None
