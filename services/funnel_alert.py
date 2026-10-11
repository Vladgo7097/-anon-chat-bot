"""Owner alert when the registration funnel stops moving.

The language-picker bug ran for a week unnoticed because nothing compared
starts against completions: the funnel report only speaks when someone opens
it. This is the watcher -- two signatures, one message per signature per day:

dead_flow  -- people keep arriving at /start but nobody reaches the menu.
              The exact shape the language bug left: arrivals alive, the
              first step swallowing everyone.
no_traffic -- nobody arrives at all: polling dead, token revoked, bot banned
              from search. Held back until waking hours, so a dead night
              does not page anyone.
"""
from datetime import timedelta

import logging
import config
from models.base import async_session
from models.operations import AnalyticsEvent
from services.notifications import enqueue
from utils.time import utcnow
from sqlalchemy import select, func

logger = logging.getLogger(__name__)

# A 24h window: below MIN_STARTS a dead stretch is a quiet day, not a bug.
# STRONG_STARTS pairs with the under-10% completion rate to catch a partial
# break -- one language or one step dead while the rest still trickles.
STARTS_WINDOW = timedelta(hours=24)
MIN_STARTS = 20
STRONG_STARTS = 100
QUIET_FROM, QUIET_TO = 0, 9  # UTC: no no-traffic page before the morning


async def _count(s, event, since):
    return await s.scalar(select(func.count(AnalyticsEvent.id)).where(
        AnalyticsEvent.event == event, AnalyticsEvent.created_at >= since)) or 0


async def check(now=None):
    """Return (signature, started, finished) for a live problem, else None.

    The owner is the only recipient; the dedupe_key carries the day so a
    broken funnel pages once instead of every ten minutes.
    """
    if not config.OWNER_ID:
        return None
    now = now or utcnow()
    since = now - STARTS_WINDOW
    async with async_session.begin() as s:
        started = await _count(s, "start_source", since)
        finished = await _count(s, "onboarding_completed", since)
        signature = None
        if started >= MIN_STARTS and (finished == 0
                                       or (started >= STRONG_STARTS and finished * 10 < started)):
            signature = "dead_flow"
            await enqueue(s, config.OWNER_ID, "funnel_alert",
                          f"funnel_alert:dead_flow:{now.date().isoformat()}",
                          f"⛔ Воронка регистрации встала. За 24 часа {started} заходов "
                          f"и {finished} завершений. Шаги открываются, но до меню не "
                          f"доходят. Найди, какой, -- как в тот раз с выбором языка.")
        elif started == 0 and not QUIET_FROM <= now.hour < QUIET_TO:
            signature = "no_traffic"
            await enqueue(s, config.OWNER_ID, "funnel_alert",
                          f"funnel_alert:no_traffic:{now.date().isoformat()}",
                          "⛔ За 24 часа в бот не пришёл ни один человек. "
                          "Проверь polling, токен и не забанен ли бот в поиске.")
    # The heartbeat is the only way to tell a quiet watcher from a dead one:
    # absence of a page must never mean absence of a check.
    logger.info("event=funnel_watch started=%s finished=%s signature=%s",
                started, finished, signature or "ok")
    if signature:
        return signature, started, finished
    return None
