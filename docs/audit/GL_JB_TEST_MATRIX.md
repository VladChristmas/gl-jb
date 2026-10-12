# Матрица тестирования GL_JB (итог аудита 2026-10-12)

Коммит `ec1df87` + рабочие изменения аудита. Итог: **489 pytest-тестов, все зелёные**;
ruff/black/mypy — 0. E2E — Playwright (Chromium, реальный HTTP-сервер, in-memory SQLite).

Условные обозначения: **A** — pytest, **E** — e2e Playwright, **S** — статическая
проверка JS/шаблонов (исходники), **M** — математика на фикстурах.

## 1. Авторизация, роли, сессии

| Сценарий | Тест | Результат |
|---|---|---|
| Аноним на `/` → редирект на /login | `test_routes.py::TestAuthRoutes` | ✅ |
| Админ на `/` → свой дашборд (логотип не разлогинивает) | `test_routes.py::test_root_redirects_admin_to_admin_dashboard` | ✅ |
| Пользователь на `/` → свой дашборд | `test_routes.py::test_root_redirects_user_to_user_dashboard` | ✅ |
| Неверный токен → ошибка без утечки токена | `test_e2e_playwright.py::test_invalid_token_shows_error`, `test_audit_logging.py` | ✅ |
| Отзыв прав: страницы админа чистят сессию | `test_roles.py`, `test_session_security.py` | ✅ |
| **Отзыв прав на JSON API** (`/admin/api/*`) | `test_routes.py::TestAdminApiRoleRecheck` (5 тестов) — **новый фикс D12** | ✅ |
| Защита сессионной куки (Secure-пересчёт на HTTP) | `test_session_security.py` | ✅ |
| Ограничение частоты логина и API | `test_rate_limits.py` | ✅ |

## 2. Заказы, фото, файлы

| Сценарий | Тест | Результат |
|---|---|---|
| Список/поиск/фильтры заказов админа | `test_routes.py`, `test_analytics_smoke.py` | ✅ |
| Загрузка фото (форма, размеры, расширения) | `test_photo_upload_form.py`, `test_photo_service.py` | ✅ |
| Контроль фотографий (панель админа) | `test_photo_control_smoke.py` | ✅ |
| IDOR на `/uploads/*` (чужие фото → 403) | `test_upload_idor.py` | ✅ |
| Восстановление фото с ПК (`/admin/api/photos/restore`) | `test_photo_restore.py` (26 тестов) | ✅ |
| Удаление фото/заказа с undo-подтверждением | `test_e2e_playwright.py::TestJsRegressions::test_confirm_delete_shows_undo_toast_before_submitting` — **новый фикс D8** | ✅ |
| Фоновая отправка email по фото | `test_email_background.py` | ✅ |

## 3. Импорт Excel / ODS

| Сценарий | Тест | Результат |
|---|---|---|
| Маппинги, превью, подтверждение | `test_import_service.py` | ✅ |
| Отчёт об ошибках: скачивание, кэп 1000, чистый импорт | `test_import_report.py` (7 тестов) | ✅ |
| **Кука отчёта ≤ 4093 байт** (отчёт на диске) | `test_import_report.py::test_session_cookie_stays_within_browser_limit` — **новый фикс D11/D18** | ✅ |
| Аудит импорта (start/end/fail) | `test_audit_logging.py::TestImportAudit` | ✅ |
| **ODS → XLSX: ведущие нули сохраняются** | `test_import_ods_script.py::test_ods_to_xlsx_preserves_sheets_headers_and_types` — **новый фикс D13** | ✅ |
| ODS: пустые листы, битый файл, полный прогон сборщиков | `test_import_ods_script.py` (5 тестов) + ручной e2e-прогон скрипта | ✅ |
| Зависимости ODS (pandas/odfpy) на чистой машине | `requirements.txt`/`pyproject.toml` — **новый фикс D7** | ✅ |

## 4. Операционная аналитика

| Сценарий | Тест | Результат |
|---|---|---|
| Математика метрик (скорость, ожидания, жалобы) | `test_operational_analysis.py` | ✅ |
| Сборщики: сопоставление листов, AssemblyOrder | `test_operational_analysis.py` (в т.ч. «3 из 4 matched» — задумано) | ✅ |
| **Смешанные часовые пояса** (aware + naive) | `test_audit_repro.py` — **новый фикс D3** | ✅ |
| Привязка данных-quality, дашборд сессии | `test_operational_analysis.py` | ✅ |

## 5. Жалобы

| Сценарий | Тест | Результат |
|---|---|---|
| Создание/дедупликация/зеркало `complaint_text` | `test_complaints.py::TestComplaintModel`, `TestComplaintImport` | ✅ |
| Страница жалоб: фильтры, поиск, даты, экспорт | `test_complaints.py::TestComplaintsPage`, `TestComplaintsExport` | ✅ |
| **Карточка жалобы рендерится (без 500)** | `test_audit_repro.py::test_complaint_detail_page_renders` — **новый фикс D1** | ✅ |
| **Смена статуса POST с карточки (раньше 405)** | `test_audit_repro.py::test_complaint_detail_status_change_post` — **новый фикс D2** | ✅ |
| Сводка по курьерам | `test_complaints.py::TestCourierSummary` | ✅ |

## 6. Рейтинг курьеров, еженедельный отчёт, панель сборщиков

| Сценарий | Тест | Результат |
|---|---|---|
| Недельные границы, атрибуция дат, формула балла | `test_courier_rating.py` | ✅ |
| KPI/подсчёт заказов | `test_order_counting.py` | ✅ |
| Панель сборщиков, pending-фильтры, API счётчика | `test_picker_dashboard.py` | ✅ |
| Еженедельный отчёт руководителя | `test_health_and_ui.py`, `test_analytics_smoke.py` | ✅ |

## 7. Чат, уведомления, UI/JS

| Сценарий | Тест | Результат |
|---|---|---|
| Чат: доступ, публикация только админом, удаление | `test_chat.py` | ✅ |
| Уведомления: дедупликация, фильтр seen | `test_picker_dashboard.py`/`test_health_and_ui.py` + API | ✅ |
| **Счётчик «Ожидают фото» обновляется живьём** | `test_e2e_playwright.py::test_pending_count_badge_refreshes_via_js` — **новый фикс D9** | ✅ |
| **XSS-экранирование уведомлений / импорта / toast** | `test_js_regressions.py` (9 тестов, S) — **новые фиксы D5, D6, D15** | ✅ |
| **Service Worker регистрируется, scope `/`** | `test_js_regressions.py` (S+интеграция) — **новый фикс D10** | ✅ |
| `manifest.json` start_url → `/` | `test_js_regressions.py` — **новый фикс D16** | ✅ |
| Мобильная вёрстка 320–1440, карточки таблиц | `test_e2e_playwright.py::TestMobileFlow`, `TestViewportMatrix` (E) | ✅ |
| Логотип/бренд не разлогинивает | `test_static_regressions.py`, e2e | ✅ |

## 8. Инфраструктура

| Сценарий | Тест | Результат |
|---|---|---|
| `/health`, JSON-ошибки для fetch-клиентов | `test_health_and_ui.py`, `test_error_responses.py` | ✅ |
| Миграции (SQLite + PostgreSQL), миграционный лок | `test_migrations.py`, `test_migrations_postgres.py`, `test_migration_lock.py` | ✅ |
| Аудит-логгеры не гаснут после create_app | `test_audit_logging.py::TestLoggersSurviveMigrations` | ✅ |
| Токены в логи не утекают | `test_token_security.py`, `test_audit_logging.py` | ✅ |
| CI-гейты: ruff / black / mypy / pytest | `.github/workflows/ci.yml` + локальный прогон | ✅ 0 / 0 / 0 / 489 |

## Открытые пункты (не блокеры, см. отчёт, раздел «Рекомендации»)

1. JS-исходники проверяются статически (S) — нет JS-юнит-тестов.
2. `sw.js` обслуживается из `/static/` через заголовок — при переходе на прямую раздачу
   статики nginx придётся вернуть `Service-Worker-Allowed` в конфиг nginx.
3. `instance/import_reports/` чистится opportunistic (24ч) — при желании добавить
   cleanup при старте приложения.
