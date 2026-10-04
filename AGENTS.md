# Проект: Система управления фото заказов (GL_JB)

## Обзор
Flask-приложение для управления заказами и фото с разделением на админку и пользовательскую часть. Пользователи входят по токену, загружают фото к своим заказам. Админ управляет пользователями, загружает заказы из Excel, просматривает всё.

---

## Текущее состояние (Что сделано)

### Backend (app.py + models.py)
- **Модели БД** (Flask-SQLAlchemy): `User`, `Order`, `Photo` с внешними ключами и каскадным удалением
- **Миграции**: Flask-Migrate (Alembic) настроен, создана начальная миграция
- **Админка**:
  - Вход по паролю (`ADMIN_PASSWORD`) и токену суперадмина (`ADMIN_TOKEN`)
  - Дашборд со статистикой (кол-во пользователей, заказов, фото)
  - CRUD пользователей: создание (автогенерация 5-символьного токена), удаление, **перегенерация токена**
  - Загрузка Excel с заказами: парсинг колонки ФИО, привязка к пользователям, сохранение колонок в description, **валидация дублей** (order_number + user_id)
  - Просмотр всех заказов с пагинацией, поиском и фильтром по пользователю
  - **Редактирование заказов** (номер, описание)
  - **Экспорт заказов в Excel**
  - Удаление заказов
- **Пользовательская часть**:
  - Вход по токену (сессия) с rate limiting
  - Список своих заказов с пагинацией и поиском
  - Загрузка фото к заказу (валидация: jpg/png/webp, 16МБ, безопасное имя файла)
  - Просмотр фото заказа (галерея с ссылками на оригиналы)
  - **Удаление отдельных фото**
- **Файлы**: отдача из `/uploads/<filename>`
- **Безопасность**: 
  - CSRF защита на всех формах (Flask-WTF)
  - Rate limiting (Flask-Limiter): 5/min на админ логины, 10/min на пользовательский вход
  - Логирование (RotatingFileHandler в `logs/app.log`)
  - Декораторы `admin_required`, `user_required`
  - `secure_filename`, параметризованные запросы через ORM
- **Production readiness**:
  - Конфиг через `.env` (python-dotenv)
  - `debug` из env (`FLASK_DEBUG`)
  - Health check endpoint (`/health`)
  - Dockerfile, docker-compose.yml, nginx.conf
  - systemd service file
  - gunicorn конфиг

### Frontend (templates/ + static/style.css)
- **Базовый шаблон** (`base.html`): флеш-сообщения, подключение CSS
- **Админка**: login, token_login, dashboard, users, upload_excel, orders, edit_order — адаптивные, с таблицами, пагинацией, поиском
- **Пользовательская**: login, orders (карточки с формами загрузки, пагинацией, поиском), order_photos (галерея с удалением фото)
- **CSS**: CSS-переменные, мобильный-first, сетки, карточки, таблицы, бейджи, флеш-сообщения, пагинация, inline forms

### Конфигурация (config.py)
- `SECRET_KEY`, `ADMIN_PASSWORD`, `ADMIN_TOKEN` — из env с дефолтами
- `DATABASE_URL`, `DATABASE_PATH`, `UPLOAD_FOLDER` — из env
- `MAX_CONTENT_LENGTH = 16MB`
- `ALLOWED_EXTENSIONS = {png, jpg, jpeg, webp}`
- `RATELIMIT_STORAGE_URL` — для Redis в продакшене
- `FLASK_ENV`, `FLASK_DEBUG` — из env

### Зависимости (requirements.txt)
- Flask==3.0.3
- openpyxl==3.1.2
- Werkzeug==3.0.3
- Flask-WTF==1.2.1 (CSRF)
- Flask-Limiter==3.8.0 (rate limiting)
- Flask-Migrate==4.0.5 (миграции)
- Flask-SQLAlchemy==3.1.1 (ORM)
- python-dotenv==1.0.1 (.env)
- gunicorn==22.0.0 (WSGI server)

---

## Выполненные задачи (Roadmap - DONE)

### Приоритет 1: Стабилизация и безопасность ✅
- [x] Добавить Flask-WTF + CSRF защиту на все формы
- [x] Настроить логирование (rotating file handler, levels)
- [x] Убрать `debug=True`, вынести секреты в .env (python-dotenv)
- [x] Добавить rate limiting (Flask-Limiter) на /login, /admin/login
- [x] Добавить миграции (Flask-Migrate / Alembic)

### Приоритет 2: UX / Качество жизни ✅
- [x] Пагинация таблиц (админка: пользователи, заказы; пользователь: заказы)
- [x] Поиск/фильтр по ФИО, токену, номеру заказа, описанию
- [x] Смена токена пользователем (админом: кнопка "Новый токен")
- [x] Удаление отдельных фото (кнопка на фото в галерее)
- [x] Валидация дублей при загрузке Excel (по order_number + user_id)

### Приоритет 3: Функционал ✅
- [x] Редактирование заказа (номер, описание) в админке
- [x] Экспорт заказов в Excel/CSV
- [x] API эндпоинты (JSON): `/health` для мониторинга

### Приоритет 4: Продакшн ✅
- [x] Dockerfile + docker-compose (app + nginx)
- [x] Nginx конфиг с проксированием и кэшированием статики
- [x] Systemd service / gunicorn конфиг
- [ ] Переход на PostgreSQL (опционально, SQLite работает для малых нагрузок)
- [ ] Backup стратегия для database.db + uploads/
- [ ] Мониторинг (Sentry, Prometheus экспортеры)

---

## Команды разработки

```bash
# Запуск (development)
cd D:\GL_JB\project
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # отредактировать при необходимости
python app.py
# Открыть http://localhost:5000

# Миграции
flask db migrate -m "Описание изменений"
flask db upgrade

# Запуск (production с Docker)
docker-compose up -d --build

# Запуск (production с systemd)
# sudo cp gl_jb.service /etc/systemd/system/
# sudo systemctl daemon-reload
# sudo systemctl enable --now gl_jb
```

---

## Структура БД

```sql
users:
  id INTEGER PK
  fio VARCHAR(255) UNIQUE NOT NULL
  token VARCHAR(10) UNIQUE NOT NULL (5 chars: A-Z, 0-9)
  created_at DATETIME

orders:
  id INTEGER PK
  user_id INTEGER FK -> users(id) ON DELETE CASCADE
  order_number VARCHAR(100)
  description TEXT
  created_at DATETIME

photos:
  id INTEGER PK
  order_id INTEGER FK -> orders(id) ON DELETE CASCADE
  filename VARCHAR(255) NOT NULL (saved: {order_id}_{timestamp}.{ext})
  original_filename VARCHAR(255) NOT NULL
  uploaded_at DATETIME
```

---

## Важные маршруты

| Метод | Путь | Описание |
|-------|------|----------|
| GET/POST | `/admin/login` | Вход админа по паролю (rate limited) |
| GET/POST | `/admin/token_login` | Вход суперадмина по токену (rate limited) |
| GET | `/admin/logout` | Выход админа |
| GET | `/admin` | Дашборд |
| GET/POST | `/admin/users` | Список + создание пользователей (пагинация, поиск) |
| POST | `/admin/users/delete/<id>` | Удаление пользователя |
| POST | `/admin/users/regenerate_token/<id>` | Перегенерация токена пользователя |
| GET/POST | `/admin/upload_excel` | Загрузка заказов из Excel (валидация дублей) |
| GET | `/admin/orders` | Все заказы (пагинация, поиск, фильтр по пользователю) |
| GET/POST | `/admin/orders/edit/<id>` | Редактирование заказа |
| POST | `/admin/orders/delete/<id>` | Удаление заказа |
| GET | `/admin/export_excel` | Экспорт всех заказов в Excel |
| GET/POST | `/login` | Вход пользователя по токену (rate limited) |
| GET | `/logout` | Выход пользователя |
| GET | `/orders` | Список заказов пользователя (пагинация, поиск) |
| POST | `/orders/<id>/upload` | Загрузка фото (CSRF защита) |
| GET | `/orders/<id>/photos` | Галерея фото заказа |
| POST | `/orders/<id>/photos/delete/<photo_id>` | Удаление фото |
| GET | `/uploads/<filename>` | Отдача файла |
| GET | `/health` | Health check (JSON) |

---

## Примечания для следующего агента
- Проект готов к продакшену: CSRF, rate limiting, логирование, миграции, Docker, systemd
- Основная логика в `app.py` — можно разбить на blueprints для масштабирования
- Шаблоны используют наследование от `base.html`, стили в `static/style.css`
- БД — SQLite файл `database.db` в корне проекта (миграции в `migrations/`)
- Загруженные фото в `uploads/` (создаётся автоматически)
- Логи в `logs/app.log` (ротация 10 файлов по 10KB)
- Для продакшена: сменить дефолтные секреты в `.env`, настроить HTTPS, рассмотреть PostgreSQL
- Rate limiting использует in-memory storage (`memory://`) — для продакшена с несколькими воркерами нужен Redis (`RATELIMIT_STORAGE_URL=redis://...`)