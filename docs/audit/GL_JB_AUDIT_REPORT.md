# Отчёт технического аудита GL_JB

- **Дата:** 2026-10-12
- **Коммит:** `ec1df87` (ветка `main`, = origin/main)
- **Исполнитель:** AI-агент (opencode), по запросу владельца
- **Объём:** воспроизведение и устранение дефектов P0–P3 по всем функциональным блокам
  (UI, авторизация, заказы, фото, импорт Excel/ODS, операционная аналитика,
  жалобы, чат, рейтинг, безопасность), регрессионные тесты, автопроверки.

## Итоги в цифрах

| Проверка | До аудита | После аудита |
|---|---|---|
| `ruff check .` | 0 | **0** |
| `black --check .` | 0 | **0** |
| `mypy app/ --ignore-missing-imports` | **43 ошибки (CI красный)** | **0** |
| `pytest tests/ -q` | 460 passed / 0 failed | **489 passed / 0 failed, 0 warnings** (+29) |

Все изменения оставлены в рабочем дереве (не закоммичены — по условию задачи).
Рабочее дерево до аудита уже содержало 27 изменённых файлов от предыдущих этапов;
аудит не перезаписывал их, а работал поверх.

## Метод

1. Инвентаризация кодовой базы (маршруты, шаблоны, JS, модели, сервисы, скрипты).
2. Воспроизведение каждого дефекта (тест/сценарий до фикса).
3. Минимальный фикс без изменения архитектуры, ролей и контрактов URL.
4. Регрессионный тест (pytest / e2e Playwright / статическая проверка JS-исходников).
5. Полный прогон гейтов: ruff → black → mypy → pytest.

## Устранённые дефекты

### P1 (критично: 500/поломка функции/безопасность)

| # | Дефект | Где | Фикс | Регресс-тест |
|---|---|---|---|---|
| D1 | Карточка жалобы падала с **500 BuildError**: `url_for('admin.edit_user')` — endpoint не существует | `app/templates/admin_complaint_detail.html:75` | `url_for('admin.orders', user_id=user.id)` + guard `{% if user %}` | `tests/test_audit_repro.py::test_complaint_detail_page_renders` |
| D2 | Смена статуса жалобы с карточки: **POST → 405** (маршрут был только GET) | `app/blueprints/admin.py` (`complaint_detail`) | `methods=["GET", "POST"]` | `tests/test_audit_repro.py::test_complaint_detail_status_change_post` |
| D3 | Операционная аналитика падала **TypeError** при смешанных часовых поясах в файлах (aware + naive datetime) | `app/services/operational_analysis_service.py` | нормализация к naive UTC (`_as_naive_utc`, `datetime.fromisoformat` с offset, `replace(tzinfo=None)`) | `tests/test_audit_repro.py` (4 теста + полный прогон со смешанными tz) |
| D4 | **mypy: 43 ошибки** — CI красный | `app/` (аннотации) | аннотации параметров/возвратов, `cast(...)`, `db.session.get` | mypy 0 (гейт) |
| D5 | XSS через уведомления: текст жалоб/ФИО курьеров попадал в `innerHTML` без экранирования | `app/static/notifications.js` | `escapeHtml()` на title/desc/label/dedup_key + `safeUrl()` на href (только внутренние пути) | `tests/test_js_regressions.py` |
| D6 | XSS в превью импорта Excel: содержимое файла через `innerHTML` | `app/templates/admin_import_excel.html` | `GLB.escapeHtml` на все поля из Excel и список ошибок | `tests/test_js_regressions.py` |
| D7 | Требования: для импорта ODS не хватало `pandas`/`odfpy` — на чистой машине скрипт падал ImportError | `requirements.txt`, `pyproject.toml` | добавлены `pandas==3.0.6`, `odfpy==1.4.1`; `python-magic` с platform-маркерами (Windows → `python-magic-bin`) | e2e-прогон `scripts/import_ods.py` |

### P2 (серьёзно: молчаливая потеря функций/данных)

| # | Дефект | Где | Фикс | Регресс-тест |
|---|---|---|---|---|
| D8 | **Удаление без подтверждения**: шаблоны вызывают `confirmDelete(...)`, а определена только `GLB.confirmDelete` → ReferenceError → форма удалялась сразу (заказы, пользователи, фото, сообщения чата) | 5 шаблонов + `app/static/utils.js` | `window.confirmDelete = GLB.confirmDelete` | e2e `test_confirm_delete_shows_undo_toast_before_submitting` |
| D9 | **Счётчик «Ожидают фото» мёртв**: `initNotifications(apiUrl, pendingUrl)` — второй аргумент игнорировался, `typeof pendingUrl` всегда false → polling не запускался | `app/static/notifications.js` | подключён `pendingUrl` + немедленное первое обновление | e2e `test_pending_count_badge_refreshes_via_js` |
| D10 | **PWA/offline мёртв**: `sw.js` нигде не регистрировался; плюс файл лежит в `/static/` → scope `/static/` вместо `/` | `app/templates/base.html`, `app/__init__.py` | регистрация в `base.html`; заголовок `Service-Worker-Allowed: /` на `/static/sw.js` | `tests/test_js_regressions.py` |
| D11 | **Отчёт импорта терялся у реальных браузеров**: полный список ошибок лежал в session-cookie ( werkzeug предупреждал: 5960 > 4093 байт; браузеры молча отбрасывают куки > 4KB) | `app/blueprints/admin.py` | отчёт на диск (`instance/import_reports/`), в сессии — указатель ~150 байт; очистка после скачивания + purge старше 24ч | `tests/test_import_report.py::test_session_cookie_stays_within_browser_limit` (кука ≤ 4093) |
| D12 | **Отзыв прав не действовал на JSON API**: `/admin/api/pending_count`, `/notifications`, `/files`, `/photos/restore` проверяли только `session["is_admin"]` без перепроверки роли в БД (в отличие от `admin_required`) | `app/blueprints/admin.py` | хелпер `_admin_session_valid()` (как в `admin_required`) на 4 эндпоинта | `tests/test_routes.py::TestAdminApiRoleRecheck` (5 тестов) |
| D13 | **Ведущие нули в ODS**: pandas приводил ID `"001"` → `1` во всех числовых колонках → связывание заказов между листами рассыпалось | `scripts/import_ods.py` | `dtype=str` при чтении ODS | `tests/test_import_ods_script.py::test_ods_to_xlsx_preserves_sheets_headers_and_types` |
| D14 | **FLASK_DEBUG по умолчанию "1"**: деплой без явной переменной окружения поднимал приложение с debug (отключённые CSP/HSTS/Secure-куки; на docker-compose путь `.env` мог утащить debug в прод) | `config.py` | дефолт `"0"` (включается только явно, `.env` разработки не затронут) | гейты зелёные; `render.yaml` и так задаёт "0" |
| D15 | XSS в toast: `GLB.showToast` вставлял сообщение (в т.ч. ФИО) через `innerHTML` | `app/static/utils.js` | `textContent` | `tests/test_js_regressions.py` |

### P3 (мелко / UX / надёжность)

| # | Дефект | Где | Фикс | Регресс-тест |
|---|---|---|---|---|
| D16 | `manifest.json` `start_url: "/orders"` — для ролей без доступа к этому разделу установленное PWA открывалось на 403 | `app/static/manifest.json` | `start_url: "/"` (роутинг по роли) | `tests/test_js_regressions.py` |
| D17 | Битый ODS-файл вываливал traceback (BadZipFile) вместо понятной ошибки | `scripts/import_ods.py` | try/except + дружелюбное сообщение + `exit 1` | `tests/test_import_ods_script.py` |
| D18 | Блок отчёта на странице импорта показывал пустое число ошибок: шаблон читал `session.import_report.total_errors`, а ключ не сохранялся | `app/blueprints/admin.py` + шаблон | `total_errors` кладётся в указатель сессии | `tests/test_import_report.py` (полный блок) |

## Наблюдения (не дефекты, закреплено документацией/тестами)

1. **`match_status` всегда `"matched"`** в операционной аналитике — задуманное поведение,
   закреплено тестом `tests/test_operational_analysis.py:264` («Только сопоставленные
   заказы сохраняются как AssemblyOrder (3 из 4)»). Не менялось.
2. **Суперадмин-сессии** (вход по `ADMIN_TOKEN`/`ADMIN_PASSWORD`) не содержат `user_id` —
   перепроверка роли в БД для них невозможна и пропускается осознанно (это не
   пользовательская учётная запись, а общий секрет).
3. **`psycopg==3.2.9`** в requirements не импортируется кодом напрямую (нативный драйвер
   для PostgreSQL) — оставлен без изменений.
4. `/uploads/*` защищён (админ-сессия или `X-Admin-Token` + проверка владельца фото) —
   проверено тестами `test_upload_idor.py`.
5. nginx в `nginx.conf` проксирует **все** маршруты (включая `/static/`) в Flask, поэтому
   заголовок `Service-Worker-Allowed` доходит и в проде.

## Что проверено и не тронуто

- Бизнес-правила (рейтинг курьеров, KPI дашборда, математика операционной аналитики).
- Контракты URL, ролей, CSRF-защиты.
- Миграции БД (не редактировались в рамках аудита).
- Логика чата, жалоб, еженедельного отчёта, панели сборщиков — покрыта существующими
  тестами, все проходят.

## Рекомендации (на будущее, не в рамках аудита)

1. Покрыть JS юнит-тестами (Node/jest) вместо статических проверок исходников.
2. Вынести `sw.js` в корень маршрута (или nginx-локейшн) и хэшировать `CACHE_NAME`
   при каждом деплое статики.
3. Добавить TTL-очистку `instance/import_reports/` при старте приложения (сейчас —
   opportunistic purge при каждом сохранении отчёта).
4. Для e2e параллелить тесты с.setViewportMatrix одним браузером (сейчас быстро, но
   при росте набора станет узким местом).

## Гейты финального прогона (2026-10-12)

```
ruff check .            -> All checks passed!
black --check .         -> 79 files would be left unchanged
mypy app/               -> Success: no issues found in 39 source files
pytest tests/ -q        -> 489 passed, 0 warnings
```
