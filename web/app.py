"""Static informational site: what the bot is and a link to open it.

No accounts, no browser search/chat/calls -- all of that lives in the bot
itself. Runs as its own container (`uvicorn web.app:app`) only because that
was already the deploy shape; it does nothing beyond serving static files
plus two counters (page views, bot-link clicks) so the owner can tell
whether the landing page is doing anything at all.

No third-party analytics: this product's whole pitch is anonymity, and
sending every visitor's IP/device fingerprint to Google would be a bad joke.
The counters below are aggregate daily totals -- no per-visitor identity is
ever stored.
"""
import mimetypes
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

import config

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("image/webp", ".webp")

app = FastAPI(title="anon-chat-bot site")
_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(config.REDIS_URL, decode_responses=True)
    return _redis


async def _bump(counter: str, days: int = 90):
    """Best-effort daily counter. A dead Redis must never break the page for
    a visitor -- losing a day of stats is fine, a broken landing page isn't."""
    from utils.time import utcnow
    from redis.exceptions import RedisError
    key = f"landing:{counter}:{utcnow():%Y-%m-%d}"
    try:
        r = _get_redis()
        await r.incr(key)
        await r.expire(key, days * 86400)
    except RedisError:
        pass


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/track/view")
async def track_view():
    await _bump("views")
    return {"ok": True}


@app.post("/api/track/click")
async def track_click():
    await _bump("bot_clicks")
    return {"ok": True}


@app.get("/api/online")
async def online():
    """Real online-user count, same source the bot itself uses -- not a
    made-up marketing number."""
    from services.chat_manager import get_online_count
    return {"online": await get_online_count()}


@app.get("/api/stats")
async def stats(request: Request):
    """Aggregate counters only, gated by a shared secret in the query string
    -- there is no login system on this site, and view counts are harmless
    but no reason to leave them world-readable either."""
    if not config.STATS_ACCESS_KEY or request.query_params.get("key") != config.STATS_ACCESS_KEY:
        raise HTTPException(status_code=404)
    from datetime import timedelta
    from redis.exceptions import RedisError
    from utils.time import utcnow
    today = utcnow()
    try:
        r = _get_redis()
        days = []
        for i in range(14):
            day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            views = int(await r.get(f"landing:views:{day}") or 0)
            clicks = int(await r.get(f"landing:bot_clicks:{day}") or 0)
            days.append({"date": day, "views": views, "bot_clicks": clicks})
        return {"days": days}
    except RedisError:
        raise HTTPException(status_code=503, detail="REDIS_UNAVAILABLE")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
