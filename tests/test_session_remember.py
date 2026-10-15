"""«Запомнить вход» (вход по тегу): сессия должна переживать закрытие браузера/PWA.

Раньше логин ставил browser-session cookie (без Expires) — закрыл вкладку/приложение,
и вход терялся. Теперь login делает session.permanent=True и кука получает
Expires на config.SESSION_LIFETIME_DAYS (30 дней). Logout и отзыв роли по-прежнему
сбрасывают сессию полностью.
"""

from app.blueprints import auth as auth_module
from config import PERMANENT_SESSION_LIFETIME


def _session_cookie(response):
    for header in response.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header
    return ""


def _is_permanent(cookie: str) -> bool:
    """True только у persistent-cookie (Expires в будущем).

    Кука-очистка («session=; Expires=1970; Max-Age=0») — это удаление сессии,
    а не запоминание входа.
    """
    low = cookie.lower()
    return "expires=" in low and "1970" not in low and "max-age=0" not in low


class TestPermanentSessionCookie:
    def test_unified_admin_login_sets_permanent_cookie(self, client):
        resp = client.post("/login", data={"token": "TEST1"}, follow_redirects=False)
        cookie = _session_cookie(resp)

        assert cookie, "после логина должна устанавливаться session-cookie"
        assert _is_permanent(cookie), (
            "кука должна быть permanent (с Expires), иначе закрытие браузера "
            "разлогинивает пользователя: " + cookie
        )

    def test_unified_user_login_sets_permanent_cookie(self, client, regular_user):
        resp = client.post("/login", data={"token": regular_user.token})
        cookie = _session_cookie(resp)

        assert cookie
        assert _is_permanent(cookie), "вход по токену пользователя тоже permanent"

    def test_admin_password_login_sets_permanent_cookie(self, client):
        resp = client.post("/admin/login", data={"password": "test-admin-password"})
        cookie = _session_cookie(resp)

        assert cookie
        assert _is_permanent(cookie)

    def test_admin_token_login_sets_permanent_cookie(self, client):
        resp = client.post("/admin/token_login", data={"token": "TEST1"})
        cookie = _session_cookie(resp)

        assert cookie
        assert _is_permanent(cookie)

    def test_failed_login_has_no_permanent_cookie(self, client):
        resp = client.post("/login", data={"token": "WRONG"})

        assert "неверный токен" in resp.get_data(as_text=True).lower()
        cookie = _session_cookie(resp)
        assert not _is_permanent(cookie), (
            "неудачный вход не должен делать сессию permanent: " + cookie
        )

    def test_permanent_lifetime_configured(self):
        assert (
            PERMANENT_SESSION_LIFETIME.days == 30
        ), "дефолт срока жизни входа — 30 дней (SESSION_LIFETIME_DAYS в .env)"

    def test_logout_still_clears_permanent_session(self, client):
        client.post("/login", data={"token": "TEST1"})
        assert client.get("/admin/").status_code == 200

        client.get("/logout")
        assert (
            client.get("/admin/").status_code == 302
        ), "logout обязан сбрасывать даже permanent-сессию"


class TestSessionSurvivesBrowserClose:
    """Имитация «закрыл сайт/приложение»: новая сессия клиента с той же cookie."""

    def _login_and_grab_cookie(self, app):
        client = app.test_client()
        resp = client.post("/login", data={"token": "TEST1"})
        cookie_header = _session_cookie(resp)
        assert cookie_header and _is_permanent(cookie_header)
        name_value = cookie_header.split(";")[0]  # "session=..."
        return name_value

    def test_admin_logged_in_after_reopen(self, app):
        name_value = self._login_and_grab_cookie(app)

        fresh = app.test_client()  # «новый браузер» после закрытия
        fresh.set_cookie("session", name_value.split("=", 1)[1], domain="localhost")

        assert (
            fresh.get("/admin/").status_code == 200
        ), "permanent-cookie должна восстанавливать вход после переоткрытия сайта"

    def test_user_logged_in_after_reopen(self, app, regular_user):
        client = app.test_client()
        resp = client.post("/login", data={"token": regular_user.token})
        cookie_header = _session_cookie(resp)
        assert _is_permanent(cookie_header)

        fresh = app.test_client()
        fresh.set_cookie(
            "session", cookie_header.split(";")[0].split("=", 1)[1], domain="localhost"
        )
        assert fresh.get("/orders").status_code == 200

    def test_revoked_role_still_denied_after_reopen(self, app, db_session):
        """Отзыв роли: user удалён — permanent-cookie не даёт доступ."""
        from app.models import User

        name_value = self._login_and_grab_cookie(app)  # ADMIN_TOKEN-вход (is_admin)
        # Для роли: создаём админ-пользователя, логинимся его токеном, удаляем.
        user = User(fio="Temp Admin", token="ADMTMP1", role="admin")
        db_session.add(user)
        db_session.commit()

        client = app.test_client()
        resp = client.post("/login", data={"token": "ADMTMP1"})
        assert _is_permanent(_session_cookie(resp))

        db_session.delete(user)
        db_session.commit()

        fresh = app.test_client()
        fresh.set_cookie(
            "session", _session_cookie(resp).split(";")[0].split("=", 1)[1], domain="localhost"
        )
        assert (
            fresh.get("/admin/").status_code == 302
        ), "удалённый/отозванный пользователь не должен проходить по старой cookie"
        _ = name_value  # cookie суперадмина в этом тесте не используется дальше


class TestSessionStartHelper:
    def test_start_session_marks_permanent(self, app):
        with app.test_request_context("/login"):
            from flask import session

            auth_module._start_session(is_admin=True)
            assert session.permanent is True
            assert session["is_admin"] is True

    def test_start_session_keeps_user_keys(self, app):
        with app.test_request_context("/login"):
            from flask import session

            auth_module._start_session(user_id=7, user_fio="Тест", user_role="user")
            assert session.permanent is True
            assert session["user_id"] == 7
            assert session["user_fio"] == "Тест"
            assert session["user_role"] == "user"
