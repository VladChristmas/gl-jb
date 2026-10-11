"""Точка входа для локального запуска (`python app.py`, run.bat, systemd).

Источник истины — пакет `app/` (фабрика `create_app`) и `wsgi.py`.
Раньше здесь был отдельный монолит со своим SQLite (`database.db`), своими
маршрутами и устаревшими шаблонами: он расходился с продакшен-версией,
игнорировал DATABASE_URL и обходил все внесённые исправления.
"""

from app import create_app

application = create_app()
app = application  # совместимость: `gunicorn app:app`, flask run

if __name__ == "__main__":
    from config import FLASK_DEBUG

    application.run(host="0.0.0.0", port=5000, debug=FLASK_DEBUG)
