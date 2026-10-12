# GL_JB — функциональный аудит production

**Дата:** 2026-10-12
**Сайт:** https://gl-jb.onrender.com (production = origin/main 6cb851e, sw cache `gl_jb-v6`)
**Метод:** Playwright (Chromium 153), авторизация через предусмотренный механизм POST `/login` (токен из защищённого локального источника `.env`, в отчёты/логи не выводился), воспроизведение причин локально против SQLite **и** PostgreSQL (docker `gl_jb_postgres`, БД `gl_jb_audit`).
**Ограничение:** только чтение на production. Мутации не выполнялись.

---

## 1. Реестр функций

| Функция | Маршрут/цепочка | Результат | Статус |
|---------|-----------------|-----------|--------|
| Вход по токену | POST `/login` → session → redirect `/admin/` | 200/302, вход работает | ✅ |
| Вход с неверным токеном | POST `/login` (e2e-тест, не на prod) | flash «Неверный токен» (покрыто тестами) | ✅ локально |
| Выход | GET `/logout` | Не выполнялся (мутация сессии — не критично, код тривиален) | ⚪ не проверено |
| Логотип: клик | `#brand-home` → модалка установки | URL не изменился, модалка открылась, закрытие работает, сессия жива | ✅ |
| Установка PWA (инструкция) | модалка `#install-modal` | Android/Chrome desktop: ручная инструкция; iOS-ветка — по коду | ⚠️ частично |
| Установка PWA (нативный prompt) | `beforeinstallprompt` | Headless Chromium не выдаёт prompt; `GLB._deferredInstallPrompt=null` | ❌ не проверено |
| Service Worker | `navigator.serviceWorker` | **Зарегистрирован, но scope = `/static/` вместо `/`** | 🐛 F-04 |
| manifest.json | `/static/manifest.json` | 200, valid, start_url `/` | ✅ |
| Нижняя навигация (mobile) | 7 пунктов | 2 из 7 ведут на 500 (Жалобы, Аналитика) | 🐛 F-01/F-02 |
| Дашборд | GET `/admin/` | 200, KPI, таблицы рендерятся | ✅ |
| Заказы: список/фильтр/пагинация | GET `/admin/orders?search=…&page=2` | 200, `search=813993` → 1 строка, `page=2` работает | ✅ |
| Заказы: редактирование (форма) | GET `/admin/orders/edit/<id>` (20 шт) | 200, форма рендерится | ✅ чтение |
| Заказы: изменение данных | POST edit/delete/send_email | — | ❌ не проверено безопасно |
| Фото заказа (просмотр) | GET `/admin/orders/952/photos` | 200, пустое состояние оформлено | ✅ |
| Фото: удаление/загрузка/restore | POST | Код проверен (`delete_photo_admin` фильтрует по `photo_id+order_id`, `restore_file` с защитой path traversal) | ⚠️ код-ревью, не e2e |
| Жалобы: список | GET `/admin/complaints` | **500** | 🐛 F-01 |
| Жалобы: деталь | GET `/admin/complaint/1`, `/2` | 200 | ✅ |
| Жалобы: статус/обработка | POST `/admin/complaint/<id>` | — | ❌ не проверено безопасно |
| Жалобы: экспорт | GET `/admin/complaints/export_excel` | 200, xlsx MIME | ✅ |
| Аналитика жалоб | GET `/admin/analytics` | **500** | 🐛 F-02 |
| Еженедельный отчёт | GET `/admin/weekly_report` | **500** | 🐛 F-03 |
| Еженедельный отчёт: экспорт | GET `/admin/weekly_report/export_excel` | 200, xlsx MIME | ✅ |
| Рейтинг курьеров | GET `/admin/couriers` | 200, KPI, таблица | ✅ |
| Карточка курьера | GET `/admin/courier/<ФИО>?week=…` (9 шт) | 200, навигация по неделям | ✅ |
| Сборщики | GET `/admin/pickers` | 200 | ✅ |
| Контроль фотографий | GET `/admin/photos` + фильтры | 200 | ✅ |
| Экспорт контроля фото | GET `/admin/photos/export` | 200, xlsx MIME | ✅ |
| Операционная аналитика (страница) | GET `/admin/operational_analysis` | 200, форма загрузки, история пуста | ✅ |
| Операционная аналитика (расчёт) | POST upload + результаты | Требует загрузки файла → мутация | ❌ не проверено безопасно |
| Импорт Excel: страницы | GET upload/import/mappings | 200 | ✅ |
| Импорт Excel: выполнение | POST preview/confirm | Мутация данных | ❌ не проверено безопасно |
| Отчёт импорта | GET `/admin/import_report` | 200 → redirect `/import_excel` (отчёта в сессии нет — ожидаемо) | ✅ |
| Пользователи: список | GET `/admin/users` | 200, таблица, токены видны админу | ✅ |
| Пользователи: смена роли/удаление/регенерация токена/создание | POST | Мутации | ❌ не проверено безопасно |
| Аудит-лог | GET `/admin/audit` | 200, форма фильтров | ✅ |
| Уведомления (API) | GET `/admin/api/notifications` | 200, JSON count=1, ссылка `target_url` рабочая | ✅ |
| Счётчик pending (API) | GET `/admin/api/pending_count` | 200 `{"pending": 3}` | ✅ |
| Файлы (API) | GET `/admin/api/files` | 200 (с сессией), 401 (аноним) | ✅ |
| Чат: просмотр | GET `/chat` | 200 (доступен и админу) | ✅ |
| Чат: отправка/удаление сообщений | POST | Мутация | ❌ не проверено безопасно |
| Экспорт заказов | GET `/admin/export_excel` | 200, xlsx MIME | ✅ |
| User-дашборд/заказы (user-роль) | GET `/dashboard`, `/orders` | Под админ-сессией → redirect `/login` (нет `user_id` — ожидаемо) | ⚠️ роль не проверена |
| Доступ анонима к админке | GET `/admin/*` | Redirect `/admin/login`; API → 401/403 JSON | ✅ |
| Страницы ошибок | 404/500/401/403/413/429 | 404 и 500 проверены визуально; остальные — только errorhandler в коде | ⚠️ частично |

**Итого: ~30 функций проверено (включая API и навигацию); ~10 мутационных — не проверены безопасно; 3 — сломаны (500).**

---

## 2. Подтверждённые дефекты

### F-01 [P1] `/admin/complaints` → 500 на production

**Шаги воспроизведения:**
1. Войти на `https://gl-jb.onrender.com/login` валидным токеном.
2. Перейти на `/admin/complaints` (или с любым фильтром: `?page=2`, `?period=week`, `?status=new`, `?search=abc`, `?date_from=2026-10-01&date_to=2026-10-12`).

**Фактически:** HTTP 500, страница «500 — Ошибка сервера». Все параметры → 500 (17 проб, 2026-10-12).

**Ожидаемо:** 200 со списком жалоб.

**Доказательства:**
- Production: `results.jsonl` (записи `adm_admin_complaints`, vp390/430/768), консоль: `Failed to load resource: 500`.
- Воспроизведение локально на PostgreSQL (пустая БД, та же схема):

```
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.GroupingError)
column "orders.courier_fio" must appear in the GROUP BY clause or be used in an aggregate function
[SQL: SELECT orders.courier_fio AS orders_courier_fio, count(orders.id) ..., count(complaints.id) ...
 GROUP BY trim(orders.courier_fio)]
```

- **Код:** `app/services/complaint_service.py:263-265` — `with_entities(Order.courier_fio, func.count(...))` + `.group_by(func.trim(Order.courier_fio))`: SELECT содержит голую колонку, а GROUP BY — её выражение. SQLite это разрешает, PostgreSQL — нет.
- **Почему тесты зелёные:** все 512 тестов гоняются на SQLite (`tests/conftest.py`), где такое поведение легально.

**Рекомендация:** группировать по исходной колонке, а trim делать в Python (Python-цикл `courier.strip()` уже есть на строке 269):
```python
.with_entities(Order.courier_fio, func.count(Complaint.id))
.group_by(Order.courier_fio)
```
Либо `group_by(func.trim(...))` + `entity(func.min(Order.courier_fio))`. Добавить прогон тестов на PostgreSQL в CI (docker service) — иначе класс багов будет повторяться.

---

### F-02 [P1] `/admin/analytics` → 500 на production

**Шаги воспроизведения:** вход → `/admin/analytics` (с любыми параметрами: `period=today|week|month|custom`, `search`, `courier`).

**Фактически:** 500 на всех вариантах.

**Ожидаемо:** 200 с аналитикой.

**Доказательства:**
- Пробы параметров: все 4 варианта → 500 (`probe_500.py`).
- Локальное воспроизведение на PostgreSQL, пошаговый вызов сервисов с rollback между проверками:

```
FAIL analytics.courier_complaint_stats -> GroupingError: column "orders.courier_fio" must appear in the GROUP BY clause...
FAIL analytics.get_filter_options      -> GroupingError: ... GROUP BY trim(orders.courier_fio)
OK   analytics.complaint_reasons / complaint_trends / status_breakdown / photo_topic_breakdown
```

- **Код:** `app/services/dashboard_service.py:909` (`courier_complaint_stats`) и `:1053` (`get_filter_options`) — тот же паттерн `SELECT Order.courier_fio ... GROUP BY trim(Order.courier_fio)`.
- Обе функции вызываются в `admin.analytics()` (`admin.py:481`, `:490`) до рендера → 500.

**Рекомендация:** та же, что F-01 (`group_by(Order.courier_fio)`, trim в Python). Строки 909 и 1053 `dashboard_service.py`.

---

### F-03 [P1] `/admin/weekly_report` → 500 (Jinja: отсутствующий ключ `pct`)

**Шаги воспроизведения:** вход → `/admin/weekly_report` (параметры `week=2026-10-05`, `week=2026-09-28` — тоже 500).

**Фактически:** 500. Локально воспроизводится **и на SQLite, и на PostgreSQL** — баг не в данных и не в диалекте.

**Ожидаемо:** 200 с KPI-карточками.

**Доказательства (полный traceback, локальный запуск):**

```
File "app/templates/admin_weekly_report.html", line 41, in block 'content'
    {{ card.value }} ({{ '%.1f'|format(card.pct) }}%)
jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'pct'
```

- **Код шаблона:** `admin_weekly_report.html:40` — `{% if card.pct is not none %}`. Jinja возвращает `Undefined` для отсутствующего ключа; `Undefined is not none` → **True** (Undefined != None), поэтому ветка `format()` выполняется и падает.
- **Код данных:** `dashboard_service.py:1239-1272` — карточки «Заказов» (line 1241), «Жалоб (шт.)» (1261) и «% заказов с жалобами» (1267) не содержат ключ `pct`.
- **Почему тесты зелёные:** `weekly_report` не покрыт ни одним тестом (`rg 'weekly_report' tests/` — 0 результатов рендеринга страницы).

**Рекомендация:** в шаблоне `{% if card.pct is defined and card.pct is not none %}` **или** добавить `"pct": None` во все карточки в `week_report()`. Плюс smoke-тест `GET /admin/weekly_report == 200` (и аналогично для complaints/analytics — эти три маршрута вообще не имели рендер-тестов).

---

### F-04 [P2] Service Worker зарегистрирован со scope `/static/` вместо `/`

**Шаги воспроизведения:**
1. Открыть любую страницу (`/login`), дождаться load.
2. Выполнить `navigator.serviceWorker.getRegistrations()`.

**Фактически:** `[{scope: "https://gl-jb.onrender.com/static/", active: true}]`; консоль: `[SW] Registered: https://gl-jb.onrender.com/static/`.

**Ожидаемо:** scope `/` (заголовок `Service-Worker-Allowed: /` сервер отдаёт — проверено HEAD-запросом; комментарий в `base.html:165-167` и `app/__init__.py:135-139` прямо говорят о замысле scope `/`).

**Причина:** `utils.js:178` — `navigator.serviceWorker.register(swUrl)` вызывается **без** опции `{scope: '/'}`. Заголовок лишь разрешает широкий scope, но не задаёт его.

**Влияние:** SW контролирует только `/static/`; его логика `networkOnly` для HTML-страниц, offline-заглушка «Нет связи с сервером» и будущая offline-работа приложения **не действуют на страницы**. PWA в offline не откроет ни одной страницы.

**Рекомендация:**
```js
navigator.serviceWorker.register(swUrl, { scope: '/' });
```
+ e2e-проверка `registration.scope === origin + '/'`.

---

### F-05 [P2] Колонка «Описание» отображает служебные поля импорта

Описано в `GL_JB_UX_UI_AUDIT.md` (P2-UX-2): `czf: МОК Бишкек 14:1; confirmed_at: …; sla: 15; sum: 1777` в списках заказов/фото. Функционально данные корректны, но формат — результат импорта, а не UX.

**Рекомендация:** рендерить `description` по шаблону (адрес/подтема), детальные `key:value` — в карточке заказа отдельным блоком.

---

### F-06 [P3] Разный формат ответа API при отказе доступа

- `GET /admin/api/files` аноним → **401** `{"error": "…"}` (проверено).
- `GET /admin/api/notifications` аноним → **403** `{"error":"forbidden"}` (проверено).
- HTML-страницы аноним → redirect на `/admin/login`.

**Рекомендация:** единообразие: 401 для «нет сессии», 403 для «сессия есть, роли нет»; единый формат `{"ok": false, "error": …}` (как в `app/__init__.py:_error_response`).

---

## 3. Отдельные проверки GL_JB (Этап 7 брифа)

### Логотип
Клик по `#brand-home` на `/admin/`: URL остался `/admin/`, открылась модалка установки; переход на `/admin/users` после закрытия — сессия активна. **Сессия не теряется.** (`func_install_modal.png`)

### Установка приложения
- Модалка открывается/закрывается, содержит ручную инструкцию. В headless Chrome модалка показала инструкцию (prompt недоступен) — в живом Chrome с `beforeinstallprompt` поведение по коду (`install.js`): Android → нативный prompt; iOS Safari → инструкция «поделиться → на экран Домой».
- **Не подтверждено:** нативный prompt на реальном Android; поведение iOS Safari; отсутствие повторного предложения в standalone (код `isStandaloneDisplay()` есть, непроверен на устройстве); открытие/закрытие окна установки не теряет авторизацию — проверено для модалки ✅.
- **Внимание:** из-за F-04 офлайн-часть PWA не работает.

### Выборочная очистка загрузок
Единицная очистка реализована: `POST /admin/orders/<order_id>/photos/delete/<photo_id>` → `PhotoService.delete_photo_admin(photo_id, order_id)` (`photo_service.py:162`) — запрос фильтруется **по обоим id одновременно**, удаление строки БД + файла, запись в аудит-лог. Происхождение файла определяется надёжно (только `photo.filename` из строки БД, путь не собирается из пользовательского ввода; в `restore_file` есть страховка от path traversal). **Массовой/интерфейсной «выборочной очистки нескольких загрузок» нет** — только поштучное удаление со страницы заказа. Рекомендуемый UX при доработке: чекбоксы в `/admin/photos` + сводка «будет удалено N файлов, X МБ» + откат через существующий restore.

### Операционная аналитика
- Страница загрузки работает (200), история пуста («Пока нет загруженных файлов»).
- Загрузка файла на production → мутация БД → **не выполнялась**. Связность «заказ ↔ сборщик ↔ курьер» в коде: `operational_analysis_service.py` сопоставляет по `order_id`; сборщик (`picker_fio`) и курьер (`courier_fio`) — раздельные поля, не смешиваются.
- Известное наблюдение из прошлого аудита: `match_status` всегда `matched`, закреплено тестом `test_operational_analysis.py:264` (не дефект).
- **Не проверено на production:** корректность расчётов метрик ожидания/скорости на реальных данных (требует загрузки тестового файла — мутация).

---

## 4. Техническая диагностика (Этап 8)

| Проверка | Результат |
|----------|-----------|
| JS pageerror на ~40 страницах | **0** |
| Console error | только `Failed to load resource: 500` (3 страницы) и `404` на несуществующей |
| Неудачные запросы (requestfailed) | 1: `GET /admin/complaints/export_excel → net::ERR_ABORTED` — это прерванная навигация на download в Playwright, сам экспорт отдаёт 200/xlsx (проверено fetch'ем); **не дефект** |
| HTTP-статусы | 3×500 (F-01…F-03); 404 — корректно; остальные 200 |
| Аноним | `/admin/*` → redirect login; `/admin/api/files` → 401; `/admin/api/notifications` → 403; `/orders`, `/dashboard` → redirect login |
| CSS/JS/иконки/манифест | все 200, битых ссылок нет |
| PWA | manifest 200 valid; sw.js 200 (cache v6 = последний деплой); **scope битый (F-04)** |
| Заголовок `Service-Worker-Allowed` | `/` — присутствует |
| Backend «жив» | `/health` 200; но 3 раздела 500 — успешная загрузка страницы сама по себе ничего не доказывает (подтверждено) |

---

## 5. Непроверенные сценарии и причины

| Сценарий | Причина |
|----------|---------|
| POST-мутации: удаление заказов/пользователов, смена ролей, регенерация токенов, импорт Excel, upload аналитики, статусы жалоб, чат | Запрет брифа: изменение реальных данных production без тестового режима |
| Role-based access: вход токеном курьера/сборщика | Нет тестового токена роли (`GL_JB_TEST_TOKEN` не задан; использовался только админский вход) |
| Нативный prompt установки PWA, standalone-повтор, iOS Safari | Headless-браузер + отсутствие реального устройства |
| Реальные Safari iOS / Chrome Android (тач-жесты, safe-area, рендер шрифтов) | Эмуляция viewport не подтверждает поведение устройства |
| Рейтинг/расчёты аналитики на реальных данных | Требуется загрузка файла (мутация) |
| Rate limit (429), 413 (файл >16 МБ), 400 (битый CSRF) | Намеренная генерация ошибок на production не выполнялась |
| Офлайн-режим SW | Действие F-04 делает его нерабочим для страниц; после фикса — повторить |

---

## 6. Итого дефектов

| Приоритет | Кол-во | IDs |
|-----------|--------|-----|
| P0 | 0 | — |
| P1 | 3 | F-01, F-02, F-03 |
| P2 | 2 | F-04, F-05 |
| P3 | 1 | F-06 |

**Корневые причины P1:**
1. **SQL-диалект:** тесты гоняются только на SQLite, где голая колонка в `GROUP BY`-выражении легальна — PostgreSQL отдаёт `GroupingError`. Класс багов будет повторяться, пока в CI нет прогона на PostgreSQL.
2. **Слабое покрытие рендера:** у `/admin/complaints` и `/admin/analytics` есть тесты (`test_complaints.py`, `test_analytics_smoke.py`), но они проходят на SQLite; у `/admin/weekly_report` рендер-тестов нет вообще, поэтому баг с `card.pct` (воспроизводится даже на SQLite) никто не поймал.

---

## 7. Статус исправлений (2026-10-12, локально, без коммита)

| ID | Статус | Изменения |
|----|--------|-----------|
| F-01 | ✅ исправлено локально | `complaint_service.py:263` — SELECT `trim(Order.courier_fio)` совпадает с GROUP BY |
| F-02 | ✅ исправлено локально | `dashboard_service.py:889, 1050` — тот же паттерн (AnalyticsService) |
| F-03 | ✅ исправлено локально | `kpi_cards` + `pct`/`delta`: None; шаблон: `card.X is defined and ... is not none` |
| F-04 | ✅ исправлено локально | `utils.js` register со scope `/`; `sw.js` cache v7 |
| F-06 | ✅ исправлено локально | `_admin_api_auth_error()`: 401 без сессии / 403 при отозванной роли |
| F-05 | ⬜ не исправлено | требует продуктового решения по формату «Описания» |

**Верификация на живой PostgreSQL** (docker `gl_jb_audit`): `courier_summary`,
`courier_complaint_stats`, `get_filter_options` — без GroupingError;
`GET /admin/complaints|analytics|weekly_report` → 200.

**Регрессионные тесты:** `tests/test_group_by_dialect.py` — компиляция агрегирующих
запросов под `postgresql`-диалект (SELECT-список не содержит голой колонки вне
GROUP BY-выражения) + smoke-рендер complaints/analytics/weekly_report и экспорт
weekly_report (раньше страница не покрыта вообще). Плюс тест scope SW и 401/403
для `/admin/api/*`. **Итого: 527 passed** (было 512).
