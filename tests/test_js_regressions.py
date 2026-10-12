"""Регрессионные тесты JS/фронта (P2-фиксы аудита).

JS не покрыт pytest напрямую, поэтому здесь проверяются исходники:
экранирование данных перед innerHTML, глобальный confirmDelete,
регистрация Service Worker и его scope.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC = PROJECT_ROOT / "app" / "static"
TEMPLATES = PROJECT_ROOT / "app" / "templates"


class TestXssEscaping:
    def test_notifications_js_escapes_server_fields(self):
        src = (STATIC / "notifications.js").read_text(encoding="utf-8")
        assert "escapeHtml(n.title)" in src
        assert "escapeHtml(n.description)" in src
        assert "escapeHtml(n.target_label)" in src
        assert "escapeHtml(n.dedup_key)" in src
        assert "safeUrl(n.target_url)" in src

    def test_showtoast_uses_textcontent_not_innerhtml(self):
        src = (STATIC / "utils.js").read_text(encoding="utf-8")
        assert "span.textContent = message" in src
        assert "escapeHtml" in src

    def test_import_excel_template_escapes_excel_data(self):
        src = (TEMPLATES / "admin_import_excel.html").read_text(encoding="utf-8")
        assert "const esc = GLB.escapeHtml;" in src
        assert "esc(data.errors" in src
        assert "${esc(item.fio" in src
        assert "${esc(recognizedStr)" in src


class TestConfirmDeleteGlobal:
    def test_utils_exposes_window_alias(self):
        src = (STATIC / "utils.js").read_text(encoding="utf-8")
        assert "window.confirmDelete = GLB.confirmDelete" in src

    def test_all_confirmdelete_callers_have_global_alias(self):
        """Каждый шаблон с confirmDelete(...) обязан иметь глобальную функцию."""
        utils = (STATIC / "utils.js").read_text(encoding="utf-8")
        assert (
            "window.confirmDelete = GLB.confirmDelete" in utils
        ), "Глобальный алиас confirmDelete удалён"
        callers = [
            p.name
            for p in TEMPLATES.glob("*.html")
            if "return confirmDelete(" in p.read_text(encoding="utf-8")
        ]
        assert callers, "Ни один шаблон не вызывает confirmDelete — тест устарел"


class TestNotificationsPendingCounter:
    def test_init_notifications_accepts_pending_url(self):
        src = (STATIC / "notifications.js").read_text(encoding="utf-8")
        assert "GLB.initNotifications = function (apiUrl, pendingUrl)" in src
        assert "typeof pendingUrl" not in src
        assert "refreshPending(pendingUrl)" in src


class TestServiceWorker:
    def test_base_template_registers_sw(self):
        src = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "GLB.registerServiceWorker(" in src

    def test_register_passes_root_scope(self):
        """F-04: register() без {scope:'/'} даёт scope /static/ — offline для
        HTML-страниц не работает (заголовок Service-Worker-Allowed лишь разрешает)."""
        src = (STATIC / "utils.js").read_text(encoding="utf-8")
        assert (
            "navigator.serviceWorker.register(swUrl, { scope: '/' })" in src
        ), "Service Worker регистрируется без scope '/' — SW не будет управлять страницами"
        assert "register(swUrl)" not in src.replace("register(swUrl, { scope: '/' })", "")

    def test_sw_script_response_allows_root_scope(self, app):
        client = app.test_client()
        resp = client.get("/static/sw.js")
        assert resp.status_code == 200
        assert resp.headers.get("Service-Worker-Allowed") == "/"

    def test_manifest_start_url_is_role_aware(self):
        manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["start_url"] == "/", (
            "start_url должен вести на / (роутинг по роли), " f"получено {manifest['start_url']!r}"
        )
