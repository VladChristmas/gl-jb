/**
 * Admin notification polling and deduplication
 * Uses sessionStorage for deduplication
 */
(function () {
    var STORAGE_KEY = 'gljb_notifications_seen';
    var POLL_INTERVAL = 60000; // 60 seconds
    var TTL_HOURS = 24;

    function getSeen() {
        try {
            var raw = sessionStorage.getItem(STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (e) {
            return {};
        }
    }

    function setSeen(seen) {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(seen));
        } catch (e) {
            // ignore quota errors
        }
    }

    function isSeen(key, seen) {
        var ts = seen[key];
        if (!ts) return false;
        var now = Date.now();
        return (now - ts) < TTL_HOURS * 3600 * 1000;
    }

    function markSeen(key, seen) {
        seen[key] = Date.now();
        setSeen(seen);
    }

    function buildSeenParam(seen) {
        return Object.entries(seen).map(function (kv) { return kv[0] + ':' + kv[1]; }).join(',');
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Внутренний путь или '#': target_url попадает в href через innerHTML
    function safeUrl(value) {
        var s = value ? String(value) : '';
        return (s.charAt(0) === '/' && s.charAt(1) !== '/') ? escapeHtml(s) : '#';
    }

    function renderNotifications(data) {
        var list = document.getElementById('notification-list');
        var badge = document.getElementById('notification-badge');
        if (!list || !badge) return;

        if (!data.notifications || data.notifications.length === 0) {
            list.innerHTML = '<div class="notification-empty">Нет уведомлений</div>';
            badge.style.display = 'none';
            badge.textContent = '';
            return;
        }

        var seen = getSeen();
        var unread = data.notifications.filter(function (n) { return !isSeen(n.dedup_key, seen); });

        if (unread.length === 0) {
            list.innerHTML = '<div class="notification-empty">Нет новых уведомлений</div>';
            badge.style.display = 'none';
            badge.textContent = '';
            return;
        }

        badge.textContent = unread.length;
        badge.style.display = 'flex';

        list.innerHTML = unread.map(function (n) {
            var timeStr = n.time_iso ? n.time_iso.slice(0, 16).replace('T', ' ') : '';
            return '<a href="' + safeUrl(n.target_url) + '" class="notification-item" data-dedup-key="' + escapeHtml(n.dedup_key) + '" role="menuitem">' +
                '<span class="notification-severity notification-' + escapeHtml(n.severity) + '"></span>' +
                '<div class="notification-content">' +
                '<div class="notification-title">' + escapeHtml(n.title) + '</div>' +
                '<div class="notification-desc">' + escapeHtml(n.description) + '</div>' +
                '<div class="notification-meta">' +
                '<span class="notification-time">' + escapeHtml(timeStr) + '</span>' +
                '<span class="notification-action">' + escapeHtml(n.target_label) + '</span>' +
                '</div>' +
                '</div>' +
                '</a>';
        }).join('');
    }

    window.GLB = window.GLB || {};
    GLB.initNotifications = function (apiUrl, pendingUrl) {
        function fetchNotifications() {
            var seen = getSeen();
            var param = buildSeenParam(seen);
            var url = apiUrl + (param ? '?seen=' + encodeURIComponent(param) : '');

            fetch(url)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data && Array.isArray(data.notifications)) {
                        renderNotifications(data);
                    }
                })
                .catch(function () { /* ignore */ });
        }

        // Dropdown toggle
        var toggle = document.getElementById('notification-toggle');
        var dropdown = document.getElementById('notification-dropdown');
        var clearBtn = document.getElementById('notification-clear');

        if (toggle && dropdown) {
            toggle.addEventListener('click', function (e) {
                e.stopPropagation();
                var expanded = toggle.getAttribute('aria-expanded') === 'true';
                toggle.setAttribute('aria-expanded', !expanded);
                dropdown.setAttribute('aria-hidden', expanded);
            });

            document.addEventListener('click', function (e) {
                if (!dropdown.contains(e.target) && e.target !== toggle) {
                    toggle.setAttribute('aria-expanded', 'false');
                    dropdown.setAttribute('aria-hidden', 'true');
                }
            });

            // Mark as seen when clicking a notification
            dropdown.addEventListener('click', function (e) {
                var item = e.target.closest('.notification-item');
                if (item) {
                    var key = item.getAttribute('data-dedup-key');
                    if (key) {
                        var seen = getSeen();
                        markSeen(key, seen);
                        // Re-render immediately
                        fetchNotifications();
                    }
                }
            });

            // Clear all seen
            if (clearBtn) {
                clearBtn.addEventListener('click', function (e) {
                    e.preventDefault();
                    e.stopPropagation();
                    var seen = getSeen();
                    // Mark all current notifications as seen
                    var list = document.getElementById('notification-list');
                    if (list) {
                        var items = list.querySelectorAll('.notification-item');
                        items.forEach(function (item) {
                            var key = item.getAttribute('data-dedup-key');
                            if (key) markSeen(key, seen);
                        });
                    }
                    fetchNotifications();
                });
            }
        }

        // Initial fetch and polling
        fetchNotifications();
        setInterval(fetchNotifications, POLL_INTERVAL);

        // Pending count polling (второй аргумент initNotifications)
        function refreshPending(url) {
            fetch(url)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (typeof data.pending !== 'number') return;
                    var badge = document.getElementById('pending-count');
                    if (badge) badge.textContent = data.pending;
                    var stat = document.getElementById('stat-pending');
                    if (stat) stat.textContent = data.pending;
                })
                .catch(function () { /* ignore */ });
        }
        if (pendingUrl) {
            refreshPending(pendingUrl);
            setInterval(function () { refreshPending(pendingUrl); }, 30000);
        }
    };
})();