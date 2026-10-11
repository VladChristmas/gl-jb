"""Regression: Secure-session cookie не должен ломать вход по чистому HTTP.

Симптом «нажал кнопку → выкинуло из аккаунта»: SESSION_COOKIE_SECURE=True при
отдаче по http:// — браузер молча отбрасывает cookie, session.clear() на
следующем запросе, пользователь снова anonymous.
"""

import app as app_pkg
from app.extensions import db


def _prod_app(monkeypatch):
    """Приложение с production-флагом SESSION_COOKIE_SECURE=True."""
    monkeypatch.setattr(app_pkg, "SESSION_COOKIE_SECURE", True)
    application = app_pkg.create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    with application.app_context():
        db.create_all()
    return application


def _session_cookie(response):
    for header in response.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header
    return ""


class TestSecureCookiePerRequest:
    def test_plain_http_login_sets_non_secure_cookie(self, monkeypatch):
        application = _prod_app(monkeypatch)
        client = application.test_client()

        resp = client.post("/login", data={"token": "TEST1"})
        cookie = _session_cookie(resp)

        assert cookie, "после логина должна устанавливаться session-cookie"
        assert "secure" not in cookie.lower(), (
            "Secure-кука на чистом HTTP отбрасывается браузером: " + cookie
        )

    def test_plain_http_login_session_survives(self, monkeypatch):
        application = _prod_app(monkeypatch)
        client = application.test_client()

        client.post("/login", data={"token": "TEST1"})
        resp = client.get("/admin/")

        assert resp.status_code == 200, "сессия должна переживать переход по ссылке"

    def test_forwarded_https_keeps_secure_cookie(self, monkeypatch):
        application = _prod_app(monkeypatch)
        client = application.test_client()

        resp = client.post(
            "/login",
            data={"token": "TEST1"},
            headers={"X-Forwarded-Proto": "https"},
        )
        cookie = _session_cookie(resp)

        assert cookie, "после логина должна устанавливаться session-cookie"
        assert "secure" in cookie.lower(), "за HTTPS Secure-кука обязательна: " + cookie

    def test_logout_clears_session(self, monkeypatch):
        application = _prod_app(monkeypatch)
        client = application.test_client()

        client.post("/login", data={"token": "TEST1"})
        assert client.get("/admin/").status_code == 200
        client.get("/logout")
        assert client.get("/admin/").status_code == 302
