/**
 * Common utilities for GL_JB
 * Loaded once and cached by browser
 */

// Apply saved theme before first paint (no flash)
(function () {
    var t = 'system';
    try { t = localStorage.getItem('theme') || 'system'; } catch (e) {}
    if (['light', 'dark', 'system'].indexOf(t) === -1) t = 'system';
    document.documentElement.setAttribute('data-theme', t);
})();

// Utility functions
window.GLB = window.GLB || {};

// Экранирование HTML: данные из сервера/Excel вставляются через innerHTML
GLB.escapeHtml = function (value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};

GLB.showToast = function (message, actionLabel, actionFn, duration) {
    duration = duration || 5000;
    var container = document.getElementById('toast-container');
    if (!container) return;
    var toast = document.createElement('div');
    toast.className = 'toast';
    var span = document.createElement('span');
    span.textContent = message; // textContent, не innerHTML: message может содержать ФИО и т.п.
    toast.appendChild(span);
    if (actionLabel && actionFn) {
        var btn = document.createElement('button');
        btn.className = 'toast-action';
        btn.textContent = actionLabel;
        btn.onclick = function () { actionFn(); dismiss(); };
        toast.appendChild(btn);
        container.appendChild(toast);
    } else {
        container.appendChild(toast);
    }
    var dismissed = false;
    function dismiss() {
        if (dismissed) return;
        dismissed = true;
        toast.classList.add('toast-out');
        setTimeout(function () { toast.remove(); }, 300);
    }
    setTimeout(dismiss, duration);
    return dismiss;
};

// Confirm with undo toast — intercepts form submit
GLB.confirmDelete = function (form, message, event) {
    if (event && event.preventDefault) event.preventDefault();
    GLB.showToast(message, 'Удалить', function () { form.submit(); }, 5000);
    return false;
};
// Глобальные алиасы: в шаблонах обработчики onsubmit вызывают confirmDelete(...) без GLB.
// Без алиаса бросался ReferenceError, и форма удалялась без подтверждения.
window.confirmDelete = GLB.confirmDelete;

// Debounced live search — auto-submit form after typing pause
GLB.enableLiveSearch = function (inputId, formSelector) {
    var input = document.getElementById(inputId);
    if (!input) return;
    var timer;
    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
            var form = input.closest(formSelector || 'form');
            if (form) form.submit();
        }, 400);
    });
};

// Theme switcher (light / dark / system)
GLB.initThemeSwitcher = function () {
    var meta = document.querySelector('meta[name="theme-color"]');
    function isDark() {
        var t = document.documentElement.getAttribute('data-theme');
        if (t === 'dark') return true;
        if (t === 'light') return false;
        return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    }
    function syncMeta() {
        if (meta) meta.setAttribute('content', isDark() ? '#1d1d1f' : '#f5f5f7');
    }
    document.querySelectorAll('.theme-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var v = btn.getAttribute('data-theme-value');
            try { localStorage.setItem('theme', v); } catch (e) {}
            document.documentElement.setAttribute('data-theme', v);
            syncMeta();
        });
    });
    syncMeta();
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').addEventListener) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncMeta);
    }
};

// P3-3: карточный вид таблиц на мобильных (<=768px).
// Из thead копируются заголовки колонок в data-label каждой ячейки —
// CSS (style.css) подписывает значения и разворачивает строку в карточку.
// Без JS таблица остаётся обычной с горизонтальным скроллом в .table-wrap.
GLB.initCardTables = function () {
    if (!window.matchMedia) return;
    var media = window.matchMedia('(max-width: 768px)');
    var applyTimer = null;

    function applyCardTables() {
        document.querySelectorAll('.table-wrap .table').forEach(function (table) {
            if (!media.matches) {
                table.classList.remove('is-cards');
                return;
            }
            table.classList.add('is-cards');
            var headers = table.querySelectorAll('thead th');
            table.querySelectorAll('tbody tr').forEach(function (row) {
                Array.prototype.forEach.call(row.children, function (cell, index) {
                    if (cell.tagName !== 'TD' || cell.hasAttribute('colspan')) {
                        cell.removeAttribute('data-label');
                        return;
                    }
                    var header = headers[index];
                    if (header) {
                        cell.setAttribute('data-label', header.textContent.trim());
                    } else {
                        cell.removeAttribute('data-label');
                    }
                });
            });
        });
    }

    function scheduleApply() {
        if (applyTimer) clearTimeout(applyTimer);
        applyTimer = setTimeout(applyCardTables, 50);
    }

    applyCardTables();
    if (media.addEventListener) media.addEventListener('change', scheduleApply);
    else if (media.addListener) media.addListener(scheduleApply);

    // Строки могут добавляться после загрузки (polling на дашборде)
    if (window.MutationObserver && document.body) {
        var observer = new MutationObserver(function () { scheduleApply(); });
        observer.observe(document.body, { childList: true, subtree: true });
    }
};

// Нижняя навигация: закрыть «Ещё» по клику вне меню / по Escape
GLB.initBottomNav = function () {
    document.addEventListener('click', function (e) {
        document.querySelectorAll('.bottom-nav-more[open]').forEach(function (details) {
            if (!details.contains(e.target)) details.removeAttribute('open');
        });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.bottom-nav-more[open]').forEach(function (details) {
                details.removeAttribute('open');
            });
        }
    });
};

// Локализованный выбор файла (P3-UX-5): имя выбранного файла рядом с кнопкой
GLB.initFileInputs = function () {
    document.querySelectorAll('input[type="file"][data-file-input]').forEach(function (input) {
        input.addEventListener('change', function () {
            var name = input.files && input.files.length ? input.files[0].name : 'Файл не выбран';
            var holder = input.closest('.file-input');
            var target = holder ? holder.querySelector('[data-file-name]') : null;
            if (target) {
                target.textContent = name;
            }
        });
    });
};

// Service Worker Registration
GLB.registerServiceWorker = function (swUrl) {
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
            navigator.serviceWorker.register(swUrl, { scope: '/' })
                .then(function (registration) {
                    console.log('[SW] Registered:', registration.scope);
                })
                .catch(function (error) {
                    console.log('[SW] Registration failed:', error);
                });
        });
    }
};

// Initialize all on DOMContentLoaded
document.addEventListener('DOMContentLoaded', function () {
    GLB.initThemeSwitcher();
    GLB.initCardTables();
    GLB.initBottomNav();
    GLB.initFileInputs();
});