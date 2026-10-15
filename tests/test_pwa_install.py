"""PWA install через логотип: исходники install.js, разметка base.html, CSS.

JS не покрыт pytest напрямую — здесь статические проверки исходников.
Платформа и модалка в браузере — tests/test_e2e_playwright.py
(TestDetectInstallPlatform, TestInstallGuideModal).
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC = PROJECT_ROOT / "app" / "static"
TEMPLATES = PROJECT_ROOT / "app" / "templates"


class TestInstallJsSource:
    def test_install_js_exists_and_exports_api(self):
        src = (STATIC / "install.js").read_text(encoding="utf-8")
        assert "GLB.detectInstallPlatform" in src
        assert "GLB.isStandaloneDisplay" in src
        assert "GLB.openInstallGuide" in src
        assert "GLB.initInstallGuide" in src

    def test_beforeinstallprompt_is_captured_without_suppressing_infobar(self):
        """Событие сохраняется, но НЕ гасится: авто-инфобар Chrome остаётся."""
        src = (STATIC / "install.js").read_text(encoding="utf-8")
        assert "window.addEventListener('beforeinstallprompt'" in src
        handler_start = src.index("window.addEventListener('beforeinstallprompt'")
        handler_snippet = src[handler_start : handler_start + 200]
        assert (
            ".prompt()" not in handler_snippet
        ), "beforeinstallprompt сразу вызывает prompt() — дублирует системный диалог"
        # prompt() вызывается только из кнопки по явному действию пользователя
        assert "promptEvent.prompt()" in src

    def test_ios_branch_never_allows_native_prompt(self):
        src = (STATIC / "install.js").read_text(encoding="utf-8")
        assert "canNativePrompt: false" in src
        assert "На экран Домой" in src
        assert "Поделиться" in src

    def test_brand_click_opens_guide_and_prevents_navigation(self):
        src = (STATIC / "install.js").read_text(encoding="utf-8")
        assert "querySelector('.topbar .brand')" in src
        assert "event.preventDefault()" in src
        assert "GLB.openInstallGuide()" in src

    def test_standalone_not_prompted_again(self):
        src = (STATIC / "install.js").read_text(encoding="utf-8")
        assert "уже установлен" in src

    def test_base_template_wires_modal_and_script(self):
        src = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "install.js" in src
        assert 'id="install-modal"' in src
        assert 'id="install-modal-body"' in src
        assert 'id="install-modal-close"' in src
        # Логотип сохраняет href для работы без JS (навигация не сломана)
        assert 'id="brand-home"' in src
        assert "url_for('main.index')" in src

    def test_sw_cache_bumped_for_new_static(self):
        src = (STATIC / "sw.js").read_text(encoding="utf-8")
        # Бамп кэша при изменении статики: фикса register scope доезжает до клиентов
        assert "gl_jb-v8" in src

    def test_modal_markup_uses_project_design_tokens(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        assert ".modal-overlay" in css
        assert ".modal-head" in css
        assert "var(--radius-lg)" in css

    def test_manifest_and_base_have_pwa_hooks(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert 'rel="manifest"' in base
        assert "apple-mobile-web-app-capable" in base
        assert "apple-touch-icon" in base
        manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["short_name"] == "GL_JB"
