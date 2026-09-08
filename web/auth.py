"""Telegram identity for the website: Login Widget (browser) and Mini App
initData verification, plus the signed session cookie that maps a request
to the same `telegram_id` the bot uses.

Both paths converge on repositories.users.UserRepository().get_or_create --
the same function /start uses -- so a person arriving from the site for the
first time gets a real account, and one who already talked to the bot gets
the same row.
"""
import hashlib
import hmac
import time
from urllib.parse import parse_qsl

from fastapi import Cookie, Depends, HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import config

SESSION_COOKIE = "session"
LOGOUT_GUARD_COOKIE = "logged_out"
LOGIN_WIDGET_MAX_AGE = 86400  # Telegram's own recommendation for auth_date freshness.


def is_fresh_after_logout(auth_date: str | None, logout_at: str | None) -> bool:
    if logout_at is None:
        return True
    if auth_date is None or not auth_date.isdigit() or not logout_at.isdigit():
        return False
    return int(auth_date) >= int(logout_at)


def init_data_auth_date(init_data: str) -> str | None:
    return dict(parse_qsl(init_data, keep_blank_values=True)).get("auth_date")


def _serializer():
    if not config.WEB_SESSION_SECRET:
        raise RuntimeError("WEB_SESSION_SECRET is not set")
    return URLSafeTimedSerializer(config.WEB_SESSION_SECRET, salt="anon-chat-web-session")


def create_session_token(telegram_id: int) -> str:
    return _serializer().dumps({"telegram_id": telegram_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=config.WEB_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    telegram_id = data.get("telegram_id")
    return int(telegram_id) if telegram_id is not None else None


def verify_login_widget(data: dict) -> bool:
    """https://core.telegram.org/widgets/login#checking-authorization"""
    received_hash = data.get("hash")
    if not received_hash or not config.BOT_TOKEN:
        return False
    auth_date = data.get("auth_date")
    if not auth_date or not auth_date.isdigit() or time.time() - int(auth_date) > LOGIN_WIDGET_MAX_AGE:
        return False
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()) if key != "hash")
    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received_hash)


def verify_miniapp_init_data(init_data: str) -> dict | None:
    """https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Returns the parsed Telegram user dict (id, first_name, username, ...) on
    success, None if the signature does not check out or is stale.
    """
    if not init_data or not config.BOT_TOKEN:
        return None
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None
    auth_date = data.get("auth_date")
    if not auth_date or not auth_date.isdigit() or time.time() - int(auth_date) > LOGIN_WIDGET_MAX_AGE:
        return None
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None
    import json
    try:
        return json.loads(data["user"])
    except (KeyError, ValueError):
        return None


async def current_telegram_id(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> int:
    telegram_id = read_session_token(session) if session else None
    if telegram_id is None:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    return telegram_id


async def current_user(telegram_id: int = Depends(current_telegram_id)):
    """Load the full User row for the session. Use current_telegram_id
    directly in routes that only need the id (cheaper); this one hits the DB."""
    from repositories.users import UserRepository
    user = await UserRepository().get(telegram_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    return user
