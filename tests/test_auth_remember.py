"""«Вход по тегу» в PWA: токен запоминается в localStorage и авто-логинит.

Standalone-приложение (iOS/Android WebView) может чистить cookies между
запусками — permanent-cookie не всегда переживает закрытие. auth-remember.js
сохраняет токен в localStorage приложения и молча входит при следующем
открытии логин-страницы. Logout очищает сохранённый токен.

E2E-часть (живой браузер) — в test_e2e_playwright.py::TestAuthRemember.
"""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


class TestAuthRememberStatic:
    def test_auth_remember_js_exists(self):
        src = (STATIC / "auth-remember.js").read_text(encoding="utf-8")
        assert src

    def test_js_stores_token_in_localstorage(self):
        src = (STATIC / "auth-remember.js").read_text(encoding="utf-8")
        assert "gljb_auth_token" in src
        assert "localStorage.setItem" in src
        assert "localStorage.getItem" in src
        assert "localStorage.removeItem" in src

    def test_js_targets_remember_forms(self):
        src = (STATIC / "auth-remember.js").read_text(encoding="utf-8")
        assert "form[data-remember-token]" in src
        assert 'input[name="token"]' in src

    def test_js_clears_token_on_logout_click(self):
        src = (STATIC / "auth-remember.js").read_text(encoding="utf-8")
        assert "a[href*='/logout']" in src

    def test_js_single_attempt_on_login_error(self):
        """Защита от цикла: при ошибке входа авто-повтор не выполняется."""
        src = (STATIC / "auth-remember.js").read_text(encoding="utf-8")
        assert "flash-error" in src

    def test_js_handles_logged_out_marker(self):
        """Logout по прямому URL: токен очищается, авто-вход не выполняется."""
        src = (STATIC / "auth-remember.js").read_text(encoding="utf-8")
        assert "logged_out" in src
        assert "history.replaceState" in src

    def test_login_forms_marked_for_remember(self):
        for name in ("unified_login.html", "admin_token_login.html"):
            src = (TEMPLATES / name).read_text(encoding="utf-8")
            assert "data-remember-token" in src, f"{name}: форма без data-remember-token"

    def test_base_template_includes_auth_remember(self):
        src = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "auth-remember.js" in src

    def test_sw_cache_bumped_for_auth_remember_js(self):
        import re

        src = (STATIC / "sw.js").read_text(encoding="utf-8")
        match = re.search(r"ASSET_VERSION\s*=\s*'(\d+)'", src)
        assert match, "sw.js должен задавать ASSET_VERSION"
        assert int(match.group(1)) >= 9, "версию кэша SW нужно поднимать при деплое"


class TestAuthRememberServer:
    def test_login_page_renders_remember_form(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "data-remember-token" in html
        assert "auth-remember.js" in html

    def test_admin_token_login_page_renders_remember_form(self, client):
        resp = client.get("/admin/token_login")
        assert resp.status_code == 200
        assert "data-remember-token" in resp.get_data(as_text=True)

    def test_auth_remember_js_served(self, client):
        resp = client.get("/static/auth-remember.js")
        assert resp.status_code == 200
        assert "gljb_auth_token" in resp.get_data(as_text=True)


class TestAuthorizedUserKeptOffLoginPage:
    """Авторизованного не показываем логин-форму (иначе авто-вход перехватывает)."""

    def test_logged_in_admin_redirected_from_login(self, client):
        client.post("/login", data={"token": "TEST1"})
        resp = client.get("/login")
        assert resp.status_code == 302
        assert "/admin" in resp.headers["Location"]

    def test_logged_in_user_redirected_from_login(self, client, regular_user):
        client.post("/login", data={"token": regular_user.token})
        resp = client.get("/login")
        assert resp.status_code == 302

    def test_anonymous_still_sees_login_form(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "data-remember-token" in resp.get_data(as_text=True)

    def test_logout_redirects_with_logged_out_marker(self, client):
        client.post("/login", data={"token": "TEST1"})
        resp = client.get("/logout")
        assert resp.status_code == 302
        assert "logged_out=1" in resp.headers["Location"]

    def test_admin_logout_redirects_with_logged_out_marker(self, client):
        client.post("/login", data={"token": "TEST1"})
        resp = client.get("/admin/logout")
        assert resp.status_code == 302
        assert "logged_out=1" in resp.headers["Location"]
