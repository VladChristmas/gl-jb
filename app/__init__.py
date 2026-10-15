import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, request
from flask_talisman import Talisman

from app.extensions import csrf, db, limiter, mail, migrate
from config import (
    DATABASE_URL,
    FLASK_DEBUG,
    MAIL_DEFAULT_SENDER,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_USE_SSL,
    MAIL_USE_TLS,
    MAIL_USERNAME,
    MAX_CONTENT_LENGTH,
    PERMANENT_SESSION_LIFETIME,
    RATELIMIT_STORAGE_URL,
    RATING_DATE_SOURCE,
    RATING_MIN_ORDERS,
    RATING_PENALTY_PER_PERCENT,
    SECRET_KEY,
    SESSION_COOKIE_SECURE,
    UPLOAD_FOLDER,
)


def create_app():
    app = Flask(__name__)

    # Render runs the app behind a proxy — take the real client IP/protocol from
    # X-Forwarded-For. Without this ALL users share one rate-limit bucket (proxy IP).
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Еженедельный рейтинг курьеров (одна бизнес-логика для всех страниц)
    app.config["RATING_MIN_ORDERS"] = RATING_MIN_ORDERS
    app.config["RATING_PENALTY_PER_PERCENT"] = RATING_PENALTY_PER_PERCENT
    app.config["RATING_DATE_SOURCE"] = RATING_DATE_SOURCE

    # Trust proxy headers (Render, nginx, etc.)
    app.config["PREFERRED_URL_SCHEME"] = "https" if not FLASK_DEBUG else "http"

    # Secure session cookies (пересчитывается per-request, см. ниже)
    app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # «Запомнить вход»: session.permanent=True при логине → кука с Expires
    # переживает закрытие браузера/приложения (см. config.SESSION_LIFETIME_DAYS).
    app.config["PERMANENT_SESSION_LIFETIME"] = PERMANENT_SESSION_LIFETIME
    app.config["REMEMBER_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    app.config["MAIL_SERVER"] = MAIL_SERVER
    app.config["MAIL_PORT"] = MAIL_PORT
    app.config["MAIL_USE_TLS"] = MAIL_USE_TLS
    app.config["MAIL_USE_SSL"] = MAIL_USE_SSL
    app.config["MAIL_USERNAME"] = MAIL_USERNAME
    app.config["MAIL_PASSWORD"] = MAIL_PASSWORD
    app.config["MAIL_DEFAULT_SENDER"] = MAIL_DEFAULT_SENDER

    Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    # P1-9: лимиты хранятся в RATELIMIT_STORAGE_URL. memory:// работает только
    # при одном воркере gunicorn/docker — при workers=2+ каждый воркер считает
    # свой счётчик, поэтому в проде нужен общий бэкенд (redis://…).
    app.config["RATELIMIT_STORAGE_URI"] = RATELIMIT_STORAGE_URL
    app.config["RATELIMIT_DEFAULT"] = "200 per day, 50 per hour"
    limiter.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)

    # Jinja2 globals: built-in functions for templates
    app.jinja_env.globals.update(max=max, min=min)

    # Initialize Celery only if Redis is available (optional)
    try:
        from app.celery_app import make_celery

        celery = make_celery(app)
        app.celery = celery  # type: ignore[attr-defined]
    except Exception:
        app.celery = None  # type: ignore[attr-defined]
        app.logger.warning("Celery not initialized (Redis not available)")

    # Initialize Talisman - configure for Render (behind proxy)
    # Render terminates SSL, so we don't force HTTPS at app level
    talisman = Talisman(
        app,
        force_https=False,  # Render handles SSL termination
        strict_transport_security=not FLASK_DEBUG,
        session_cookie_secure=SESSION_COOKIE_SECURE,
        content_security_policy=(
            {
                "default-src": "'self'",
                "script-src": "'self' 'unsafe-inline'",
                "style-src": "'self' 'unsafe-inline'",
                "img-src": "'self' data:",
                "font-src": "'self'",
            }
            if not FLASK_DEBUG
            else None
        ),
    )

    # Secure-кука пересчитывается на каждый запрос.
    # Причина: SESSION_COOKIE_SECURE=True на чистом HTTP — браузер молча отбрасывает
    # cookie, и пользователь «выпадает» из аккаунта после первой же ссылки.
    # request.is_secure учитывает X-Forwarded-Proto (ProxyFix): на Render/HTTPS
    # кука остаётся Secure, на локальном http://localhost и nginx без TLS — нет.
    # Регистрируется ПОСЛЕ Talisman, чтобы перекрыть его
    # _force_https(), который на каждом запросе снова ставит SESSION_COOKIE_SECURE=True.
    @app.before_request
    def _secure_cookie_per_request():
        secure = SESSION_COOKIE_SECURE and request.is_secure
        app.config["SESSION_COOKIE_SECURE"] = secure
        app.config["REMEMBER_COOKIE_SECURE"] = secure
        talisman.session_cookie_secure = secure

    # Service Worker должен управлять всем приложением (scope '/'), но файл
    # отдаётся из /static/ — без этого заголовка браузер отклонит регистрацию
    # с широким scope (nginx в данном проекте проксирует /static/ в Flask,
    # поэтому заголовок доходит и в проде).
    @app.after_request
    def _service_worker_scope(response):
        if request.path == "/static/sw.js":
            response.headers["Service-Worker-Allowed"] = "/"
        return response

    from app.blueprints.admin import admin_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp
    from app.blueprints.user import user_bp
    from app.utils.http import wants_json

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(main_bp)

    def _error_response(status: int, message: str) -> "tuple | None":
        """AJAX/fetch-клиенты ждут JSON, обычные браузеры — HTML-страницу (P1-10)."""
        if wants_json(request):
            return jsonify({"ok": False, "error": message}), status
        return None

    @app.errorhandler(401)
    def unauthorized(e):
        response = _error_response(401, "Требуется вход")
        if response:
            return response
        from flask import render_template

        return render_template("errors/401.html"), 401

    @app.errorhandler(403)
    def forbidden(e):
        response = _error_response(403, "Доступ запрещён")
        if response:
            return response
        from flask import render_template

        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        response = _error_response(404, "Страница не найдена")
        if response:
            return response
        from flask import render_template

        return render_template("errors/404.html"), 404

    @app.errorhandler(400)
    def bad_request(e):
        from flask import render_template

        desc = getattr(e, "description", "") or ""
        hint = None
        if "CSRF" in desc:
            hint = "Защита от поддельных запросов не пропустила форму. Обновите страницу и повторите действие."
        elif "too large" in desc.lower():
            hint = "Файл слишком большой. Максимальный размер — 16 МБ."

        message = hint or desc or "Некорректный запрос"
        response = _error_response(400, message)
        if response:
            return response
        return render_template("errors/400.html", hint=hint), 400

    @app.errorhandler(413)
    def too_large(e):
        response = _error_response(413, "Файл слишком большой. Максимальный размер — 16 МБ.")
        if response:
            return response
        from flask import render_template

        return render_template("errors/413.html"), 413

    @app.errorhandler(429)
    def rate_limited(e):
        response = _error_response(429, "Слишком много запросов. Попробуйте позже.")
        if response:
            return response
        from flask import render_template

        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def server_error(e):
        response = _error_response(500, "Внутренняя ошибка сервера")
        if response:
            return response
        from flask import render_template

        return render_template("errors/500.html"), 500

    if not app.debug:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(log_dir / "app.log", maxBytes=10240, backupCount=10)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]")
        )
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info("GL_JB startup")

        # P3-4: аудит-события (login / import / complaints) пишутся в тот же
        # файл. create_app() может вызываться многократно — handler общий.
        from app.utils.audit import audit_logger

        if not any(isinstance(handler, RotatingFileHandler) for handler in audit_logger.handlers):
            audit_logger.addHandler(file_handler)
        audit_logger.setLevel(logging.INFO)
        audit_logger.propagate = True
        audit_logger.info("GL_JB startup")

    with app.app_context():
        # Порядок: migration -> application.
        # db.create_all() как fallback после неудачной миграции ЗАПРЕЩЁН:
        # на PostgreSQL create_all не меняет существующие таблицы, из-за чего
        # схема молча разъезжалась, а ручные ALTER TABLE при старте скрывали
        # настоящую причину. Сбой миграции теперь валит старт приложения.
        # run_migrations() дополнительно сериализует воркеры gunicorn/docker,
        # иначе они гоняются за одними CREATE TABLE и роняют свой воркер.
        from app.utils.migration_lock import run_migrations

        try:
            run_migrations(app)
        except Exception:
            app.logger.exception("Database migration FAILED — application will not start")
            raise
        app.logger.info("Database migrations applied")

        try:
            from sqlalchemy import inspect

            tables = inspect(db.engine).get_table_names()
            app.logger.info(f"Tables in database: {tables}")
        except Exception as e:
            app.logger.error(f"Database inspection error: {e}")

    return app
