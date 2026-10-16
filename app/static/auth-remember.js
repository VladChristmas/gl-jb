/* Запоминание входа по токену («вход по тегу») для PWA.
 *
 * Проблема: установленное приложение (iOS/Android standalone, WebView) может
 * чистить cookies между запусками — permanent-cookie не всегда переживает
 * закрытие. localStorage приложения живёт дольше.
 *
 * Механика:
 *  - форма с data-remember-token: при отправке токен сохраняется в localStorage;
 *  - при открытии логин-страницы с сохранённым токеном — молчаливый повторный
 *    вход (одна попытка; если сервер ответил ошибкой — авто-вход не повторяется,
 *    чтобы не зациклиться);
 *  - клик по любой ссылке «Выйти» (/logout) очищает сохранённый токен.
 *
 * Безопасность: токен лежит в localStorage устройства (как и сама подписанная
 * session-cookie). XSS-защита проекта (escapeHtml/textContent) — барьер;
 * чужой физический доступ к устройству = физический доступ к тегу.
 */
(function () {
    "use strict";

    var KEY = "gljb_auth_token";

    function rememberForm() {
        return document.querySelector("form[data-remember-token]");
    }

    function hasLoginError() {
        return !!document.querySelector(".flash-error");
    }

    function saveToken(token) {
        try {
            if (token) {
                localStorage.setItem(KEY, token);
            } else {
                localStorage.removeItem(KEY);
            }
        } catch (e) {
            /* private mode / storage disabled — работаем без запоминания */
        }
    }

    function loadToken() {
        try {
            return localStorage.getItem(KEY);
        } catch (e) {
            return null;
        }
    }

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!form || !form.matches || !form.matches("form[data-remember-token]")) {
            return;
        }
        var input = form.querySelector('input[name="token"]');
        saveToken(input ? input.value.trim() : "");
    });

    document.addEventListener("click", function (event) {
        var link = event.target && event.target.closest
            ? event.target.closest("a[href*='/logout']")
            : null;
        if (link) {
            saveToken("");
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        var form = rememberForm();

        // Выход: сервер редиректит на /login?logged_out=1 — чистим токен
        // (покрывает и прямой переход на /logout без клика по ссылке).
        var params = new URLSearchParams(window.location.search);
        if (params.has("logged_out")) {
            saveToken("");
            params.delete("logged_out");
            var clean = window.location.pathname + (params.toString() ? "?" + params.toString() : "");
            window.history.replaceState(null, "", clean);
        }

        if (!form) {
            return;
        }
        var input = form.querySelector('input[name="token"]');
        if (!input || input.value) {
            return;
        }
        if (hasLoginError()) {
            return; /* неудачный вход — не повторяем автоматически */
        }
        var token = loadToken();
        if (token) {
            input.value = token;
            form.submit();
        }
    });
})();
