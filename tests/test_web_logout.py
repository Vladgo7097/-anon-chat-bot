import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

config.WEB_COOKIE_SECURE = True

from web.app import app  # noqa: E402


def test_logout_expires_the_session_cookie_with_matching_attributes():
    client = TestClient(app)
    client.cookies.set("session", "signed-value")
    response = client.post("/auth/logout")

    assert response.status_code == 200
    cookie = response.headers.get_list("set-cookie")
    assert any("session=" in value and "Max-Age=0" in value for value in cookie)
    assert any("logged_out=1" in value and "HttpOnly" in value for value in cookie)
    assert all("Path=/" in value and "SameSite=lax" in value and "Secure" in value for value in cookie)


def test_logout_guard_blocks_replayed_telegram_callback():
    client = TestClient(app, follow_redirects=False)
    client.cookies.set("logged_out", "1")

    response = client.get("/auth/telegram/callback?id=555")

    assert response.status_code == 302
    assert response.headers["location"] == "/?logged_out=1"


def test_prepare_login_removes_logout_guard():
    client = TestClient(app)
    client.cookies.set("logged_out", "1")

    response = client.post("/auth/prepare-login")

    assert response.status_code == 200
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_logged_out_page_suppresses_automatic_miniapp_login():
    page = (Path(__file__).resolve().parents[1] / "web/static/index.html").read_text(encoding="utf-8")

    assert "miniApp && !loggedOut" in page
    assert "if (!loggedOut)" in page
    assert 'id="login-again"' in page
    assert "Вы вышли из профиля." in page
