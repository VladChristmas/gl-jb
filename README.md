# GL_JB — Система управления фото заказов

Flask-приложение для управления заказами и прикреплёнными фото. Разделение на админку и пользовательскую часть.

## Возможности

### Администратор
- Вход по паролю или токену суперадмина
- Дашборд со статистикой
- Управление пользователями (создание, удаление, смена токена)
- Загрузка заказов из Excel (автопривязка по ФИО, защита от дублей)
- Просмотр всех заказов с пагинацией, поиском и фильтрами
- Редактирование заказов
- Экспорт заказов в Excel

### Пользователь
- Вход по персональному токену
- Список своих заказов с поиском и пагинацией
- Загрузка фото к заказу (JPG, PNG, WebP до 16 МБ)
- Просмотр фото в галерее
- Удаление собственных фото

## Технологии

- **Backend**: Flask 3, Flask-SQLAlchemy, Flask-Migrate, Flask-WTF, Flask-Limiter
- **Database**: SQLite (поддерживается PostgreSQL через DATABASE_URL)
- **Frontend**: Jinja2 templates, CSS Variables, Mobile-first responsive
- **Production**: Gunicorn, Nginx, Docker, Systemd

## Быстрый старт

```bash
# Клонирование и настройка
cd D:\GL_JB\project
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Конфигурация
cp .env.example .env
# Отредактируйте .env при необходимости

# Инициализация БД
flask db upgrade

# Запуск
python app.py
# Откройте http://localhost:5000
```

## Учётные данные

| Роль | Параметр | Где взять |
|------|----------|----------------------|
| Админ (пароль) | `ADMIN_PASSWORD` | задаётся только в `.env` (дефолта нет) |
| Суперадмин (токен) | `ADMIN_TOKEN` | задаётся только в `.env` (дефолта нет) |

> **Важно**: секретов в репозитории нет — задайте их в `.env` самостоятельно.
> Если токен/пароль ранее попадали в git — смените их.

## Структура проекта

```
project/
├── app.py              # Основное приложение
├── models.py           # SQLAlchemy модели
├── config.py           # Конфигурация из .env
├── requirements.txt    # Зависимости
├── .env.example        # Пример конфигурации
├── .env                # Ваша конфигурация (не в git)
├── database.db         # SQLite БД
├── migrations/         # Alembic миграции
├── uploads/            # Загруженные фото
├── logs/               # Логи приложения
├── static/
│   └── style.css       # Стили
├── templates/          # Jinja2 шаблоны
├── Dockerfile          # Docker образ
├── docker-compose.yml  # Docker Compose
├── nginx.conf          # Nginx конфиг
└── gl_jb.service       # Systemd unit
```

## Основные маршруты

| Путь | Описание |
|------|----------|
| `/` | Редирект на вход пользователя |
| `/login` | Вход пользователя по токену |
| `/admin/login` | Вход админа паролем |
| `/admin/token_login` | Вход суперадмина токеном |
| `/admin` | Дашборд админа |
| `/admin/users` | Управление пользователями |
| `/admin/upload_excel` | Загрузка заказов из Excel |
| `/admin/orders` | Все заказы |
| `/admin/orders/edit/<id>` | Редактирование заказа |
| `/admin/export_excel` | Экспорт заказов |
| `/orders` | Заказы пользователя |
| `/orders/<id>/upload` | Загрузка фото |
| `/orders/<id>/photos` | Галерея фото |
| `/health` | Health check (JSON) |

## Формат Excel для импорта

Обязательная колонка с ФИО (названия: `ФИО`, `fio`, `ФИО клиента`, `Фамилия Имя Отчество`). Остальные колонки сохраняются в описание заказа.

Пример:
| ФИО | Номер заказа | Товар | Количество |
|-----|--------------|-------|------------|
| Иванов Иван Иванович | ORD-001 | Виджет А | 5 |
| Петров Петр Петрович | ORD-002 | Гаджет Б | 3 |

## Продакшн деплой

### Docker
```bash
docker-compose up -d --build
# Приложение на порту 80 (через nginx)
```

### Systemd (Ubuntu/Debian)
```bash
sudo cp gl_jb.service /etc/systemd/system/
sudo mkdir -p /opt/gl_jb /var/log/gl_jb
sudo chown -R www-data:www-data /opt/gl_jb /var/log/gl_jb
# Скопируйте проект в /opt/gl_jb, настройте .env
sudo systemctl daemon-reload
sudo systemctl enable --now gl_jb
# Настройте nginx как reverse proxy на порт 5000
```

## Переменные окружения (.env)

```bash
# Flask
SECRET_KEY=ваш-сложный-секрет
FLASK_ENV=production
FLASK_DEBUG=0

# Admin
ADMIN_PASSWORD=сложный-пароль
ADMIN_TOKEN=длинный-рандомный-токен

# Database
DATABASE_URL=sqlite:///database.db
# Или для PostgreSQL:
# DATABASE_URL=postgresql://user:pass@localhost/dbname

# Uploads
UPLOAD_FOLDER=uploads
MAX_CONTENT_LENGTH=16777216

# Rate limiting (для продакшена с несколькими воркерами)
RATELIMIT_STORAGE_URL=redis://localhost:6379/0
```

## Разработка

```bash
# Создание миграции после изменений моделей
flask db migrate -m "Описание изменений"
flask db upgrade

# Запуск тестов (если добавите)
pytest
```

## Лицензия

MIT