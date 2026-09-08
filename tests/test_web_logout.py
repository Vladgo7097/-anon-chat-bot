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
    cookie = response.headers["set-cookie"]
    assert "session=" in cookie
    assert "Max-Age=0" in cookie
    assert "Path=/" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie


def test_logged_out_page_suppresses_automatic_miniapp_login():
    page = (Path(__file__).resolve().parents[1] / "web/static/index.html").read_text(encoding="utf-8")

    assert "miniApp && !loggedOut" in page
    assert "Вы вышли из профиля." in page
