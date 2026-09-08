"""FastAPI entry point for the website. Runs as its own container
(`uvicorn web.app:app`), separate from the bot's aiogram polling process,
sharing the same Postgres/Redis and the same services/repositories/models.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import config
from web import auth
from web.routes_account import router as account_router
from web.routes_call import router as call_router
from web.routes_chat import router as chat_router
from web.ws import router as ws_router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="anon-chat-bot web")
app.include_router(chat_router)
app.include_router(account_router)
app.include_router(call_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    telegram_id = auth.read_session_token(token) if token else None
    if telegram_id is None:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    from repositories.users import UserRepository
    from services.chat_manager import get_online_count
    from services.onboarding import step_for
    from utils.db import is_premium
    user = await UserRepository().get(telegram_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    return {
        "telegram_id": telegram_id,
        "anon_id": user.anon_id,
        "onboarding_complete": step_for(user) == "MENU",
        "bot_username": config.BOT_USERNAME,
        "is_premium": await is_premium(telegram_id),
        "online": await get_online_count(),
    }


@app.post("/auth/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        auth.SESSION_COOKIE,
        path="/",
        secure=config.WEB_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    return response


def _issue_session(response, telegram_id: int):
    token = auth.create_session_token(telegram_id)
    response.set_cookie(
        auth.SESSION_COOKIE, token, max_age=config.WEB_SESSION_MAX_AGE,
        httponly=True, secure=config.WEB_COOKIE_SECURE, samesite="lax",
    )


@app.get("/auth/telegram/callback")
async def telegram_login_callback(request: Request):
    """Redirect target of the Telegram Login Widget (browser)."""
    data = dict(request.query_params)
    if not auth.verify_login_widget(data):
        raise HTTPException(status_code=401, detail="BAD_SIGNATURE")
    telegram_id = int(data["id"])
    from repositories.users import UserRepository
    await UserRepository().get_or_create(telegram_id, username=data.get("username"), first_name=data.get("first_name"))
    response = RedirectResponse(url="/app.html", status_code=302)
    _issue_session(response, telegram_id)
    return response


@app.post("/auth/miniapp")
async def miniapp_login(payload: dict):
    """Telegram Mini App: the page posts window.Telegram.WebApp.initData here."""
    user_data = auth.verify_miniapp_init_data(payload.get("init_data", ""))
    if user_data is None or "id" not in user_data:
        raise HTTPException(status_code=401, detail="BAD_SIGNATURE")
    telegram_id = int(user_data["id"])
    from repositories.users import UserRepository
    await UserRepository().get_or_create(
        telegram_id, username=user_data.get("username"), first_name=user_data.get("first_name"))
    response = JSONResponse({"ok": True})
    _issue_session(response, telegram_id)
    return response


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
