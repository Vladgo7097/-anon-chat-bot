"""Static informational site: what the bot is and a link to open it.

No accounts, no browser search/chat/calls -- all of that lives in the bot
itself. Runs as its own container (`uvicorn web.app:app`) only because that
was already the deploy shape; it does nothing beyond serving static files
plus two counters (page views, bot-link clicks) so the owner can tell
whether the landing page is doing anything at all.

No third-party analytics: this product's whole pitch is anonymity, and
sending every visitor's IP/device fingerprint to Google would be a bad joke.
The counters below are aggregate daily totals -- no per-visitor identity is
ever stored. The one exception is a one-way hash of the visitor's IP, kept
under a day, used only to stop a script from inflating the counters by
hammering /api/track/*; the address itself is never written anywhere.
"""
import hashlib
import mimetypes
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import config

STATIC_DIR = Path(__file__).parent / "static"
# Every directory that has its own index.html, as a rooted path without
# the trailing slash: "/vozmozhnosti", "/en/faq", and so on.
PAGE_DIRECTORIES = {"/" + page.parent.relative_to(STATIC_DIR).as_posix()
                    for page in (Path(__file__).parent / "static").rglob("index.html")
                    if page.parent != STATIC_DIR}
mimetypes.add_type("image/webp", ".webp")


# Nothing was sent with a Cache-Control at all, so every asset was
# revalidated on each page load -- cheap 304s, but a round trip each.
# Filenames are not content-hashed, so the lifetimes are deliberately modest:
# a stale asset can only linger this long after a deploy.
CACHE_SECONDS = ((".html", 0), (".xml", 3600), (".txt", 3600),
                 (".css", 86400), (".js", 86400),
                 (".webp", 2592000), (".jpg", 2592000), (".png", 2592000),
                 (".ico", 2592000), (".svg", 2592000))


def cache_control(name: str) -> str:
    """HTML must revalidate: it is what changes on every deploy."""
    # Панель обновляется одновременно с API: суевый ей сутки устаревшего
    # app.js, который ломается о новый формат ответа.
    if "/admin/" in name:
        return "no-cache"
    for suffix, seconds in CACHE_SECONDS:
        if name.endswith(suffix):
            return "no-cache" if seconds == 0 else f"public, max-age={seconds}"
    return "no-cache"


class CachedStaticFiles(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = cache_control(str(full_path).lower())
        return response


app = FastAPI(title="anon-chat-bot site")
_redis = None


@app.middleware("http")
async def permanent_trailing_slash(request: Request, call_next):
    """Answer /vozmozhnosti with a 301 to /vozmozhnosti/.

    StaticFiles redirects a directory itself, but with a 307, and Google asks
    for a permanent redirect so the two spellings are consolidated into one
    address. Doing it here also keeps the redirect on https: the 307 was built
    from the request scheme, which read as http until uvicorn was told to
    trust the proxy's X-Forwarded-Proto.
    """
    path = request.url.path
    if request.method in ("GET", "HEAD") and path in PAGE_DIRECTORIES:
        # Read the forwarded scheme here rather than relying on uvicorn's
        # --forwarded-allow-ips: the redirect then stays on https however the
        # process happens to be started, and it can be tested without a proxy.
        target = request.url.replace(path=path+"/")
        forwarded = request.headers.get("x-forwarded-proto")
        if forwarded in ("http", "https"):
            target = target.replace(scheme=forwarded)
        return RedirectResponse(str(target), status_code=301)
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def not_found(request: Request, exc: StarletteHTTPException):
    """Themed 404 instead of a bare {"detail": "Not Found"} JSON blob --
    picks the RU or EN page by path prefix, same as the rest of the site."""
    if exc.status_code == 404:
        # Named 404-ru.html/404-en.html/404-uk.html (not 404.html) on purpose:
        # Starlette's StaticFiles(html=True) auto-serves a root-level 404.html
        # itself for any unmatched path -- bypassing this handler entirely and
        # always answering in Russian regardless of /en/ or /uk/ -- so the real
        # files live under names it won't recognize, and this handler picks
        # between them.
        if request.url.path.startswith("/en/"):
            page = "404-en.html"
        elif request.url.path.startswith("/uk/"):
            page = "404-uk.html"
        else:
            page = "404-ru.html"
        return FileResponse(STATIC_DIR / page, status_code=404,
                            headers={"Cache-Control": "no-cache"})
    return await http_exception_handler(request, exc)


def _get_redis():
    global _redis
    if _redis is None:
        # Same fix as the bot's own Redis pool (services/chat_manager.py):
        # without a socket timeout a stalled connection hangs the request
        # forever instead of raising, which the RedisError handlers below
        # already expect to catch.
        _redis = aioredis.from_url(config.REDIS_URL, decode_responses=True,
                                    socket_connect_timeout=3, socket_timeout=5, health_check_interval=30)
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


def _visitor_hash(request: Request) -> str:
    """One-way, unsalted hash of the caller's IP -- never the address itself.

    uvicorn is started with --forwarded-allow-ips so request.client already
    carries the real visitor IP from nginx's X-Forwarded-For, not the proxy's.
    """
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:24]


async def _first_today(kind: str, request: Request) -> bool:
    """True only the first time this visitor hits `kind` today.

    Without this, a script hitting /api/track/* repeatedly could inflate the
    counters shown on the page as a trust signal; a real visitor's own
    double-click or page reload just stops moving the numbers further.
    """
    from utils.time import utcnow
    from redis.exceptions import RedisError
    key = f"landing:seen:{kind}:{utcnow():%Y-%m-%d}:{_visitor_hash(request)}"
    try:
        r = _get_redis()
        # NX+EX in one round trip: True only the first time today, and the
        # marker outlives the day itself so a UTC-boundary visit can't double up.
        return bool(await r.set(key, "1", nx=True, ex=90000))
    except RedisError:
        # Redis is already unreachable, so the increment this guards will
        # no-op anyway (_bump catches the same error) -- don't count twice
        # by treating an unreadable dedupe check as "go ahead and count".
        return False


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/track/view")
async def track_view(request: Request):
    from redis.exceptions import RedisError
    if not await _first_today("view", request):
        return {"ok": True}
    await _bump("views")
    try:
        r = _get_redis()
        await r.incr("landing:views:total")
    except RedisError:
        pass
    return {"ok": True}


@app.get("/api/visits")
async def visits():
    """Public, honest running total of page loads since the counter was
    added -- not unique visitors (we don't fingerprint anyone), just views."""
    from redis.exceptions import RedisError
    try:
        r = _get_redis()
        total = int(await r.get("landing:views:total") or 0)
        return {"total": total}
    except RedisError:
        return {"total": None}


@app.post("/api/track/click")
async def track_click(request: Request):
    if not await _first_today("click", request):
        return {"ok": True}
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
    # Постоянное по времени сравнение -- контрастная точка != на строках
    # (L2); _guard в web/admin.py уже работает через compare_digest.
    import hmac
    supplied = request.query_params.get("key", "").encode("utf-8", "replace")
    if not config.STATS_ACCESS_KEY or not hmac.compare_digest(
            supplied, config.STATS_ACCESS_KEY.encode("utf-8", "replace")):
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


# Локальная админка: регистрируется до mount("/"), иначе Mount перехватит
# каждый путь и маршруты ниже него никогда не сработают.
from web.admin import page_router, router as admin_router  # noqa: E402

app.include_router(admin_router)
app.include_router(page_router)

app.mount("/", CachedStaticFiles(directory=STATIC_DIR, html=True), name="static")
