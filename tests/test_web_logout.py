import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

config.WEB_COOKIE_SECURE = True

from web.app import app  # noqa: E402


def test_logo_is_served_as_webp():
    response = TestClient(app).get("/img/logo.webp")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_logout_expires_the_session_cookie_with_matching_attributes():
    client = TestClient(app)
    client.cookies.set("session", "signed-value")
    response = client.post("/auth/logout")

    assert response.status_code == 200
    cookie = response.headers.get_list("set-cookie")
    assert any("session=" in value and "Max-Age=0" in value for value in cookie)
    assert any("logged_out=" in value and "Max-Age=0" not in value and "HttpOnly" in value for value in cookie)
    assert all("Path=/" in value and "SameSite=lax" in value and "Secure" in value for value in cookie)


def test_logout_guard_requires_fresh_telegram_authorization():
    from web.auth import is_fresh_after_logout

    assert not is_fresh_after_logout("100", "101")
    assert is_fresh_after_logout("101", "101")
    assert is_fresh_after_logout("100", None)


def test_logged_out_page_suppresses_automatic_miniapp_login():
    page = (Path(__file__).resolve().parents[1] / "web/static/index.html").read_text(encoding="utf-8")

    assert "miniApp && !loggedOut" in page
    assert 'id="login-again"' not in page
    assert "Сеанс завершён. Войдите через Telegram заново." in page
    assert "Другой аккаунт: нажмите аватар справа" in page
