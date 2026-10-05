import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from flask import Flask
from flask_talisman import Talisman

from app.extensions import db, csrf, limiter, migrate, mail
from config import (
    SECRET_KEY, DATABASE_URL, UPLOAD_FOLDER, MAX_CONTENT_LENGTH,
    RATELIMIT_STORAGE_URL, FLASK_DEBUG,
    MAIL_SERVER, MAIL_PORT, MAIL_USE_TLS, MAIL_USE_SSL,
    MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER
)


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Trust proxy headers (Render, nginx, etc.)
    app.config['PREFERRED_URL_SCHEME'] = 'https' if not FLASK_DEBUG else 'http'
    
    # Secure session cookies
    app.config['SESSION_COOKIE_SECURE'] = not FLASK_DEBUG
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_SECURE'] = not FLASK_DEBUG
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'

    app.config['MAIL_SERVER'] = MAIL_SERVER
    app.config['MAIL_PORT'] = MAIL_PORT
    app.config['MAIL_USE_TLS'] = MAIL_USE_TLS
    app.config['MAIL_USE_SSL'] = MAIL_USE_SSL
    app.config['MAIL_USERNAME'] = MAIL_USERNAME
    app.config['MAIL_PASSWORD'] = MAIL_PASSWORD
    app.config['MAIL_DEFAULT_SENDER'] = MAIL_DEFAULT_SENDER

    Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    app.config['RATELIMIT_STORAGE_URI'] = RATELIMIT_STORAGE_URL
    app.config['RATELIMIT_DEFAULT'] = "200 per day, 50 per hour"
    limiter.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)

    # Initialize Celery only if Redis is available (optional)
    try:
        from app.celery_app import make_celery
        celery = make_celery(app)
        app.celery = celery
    except Exception:
        app.celery = None
        app.logger.warning('Celery not initialized (Redis not available)')

    # Initialize Talisman - configure for Render (behind proxy)
    # Render terminates SSL, so we don't force HTTPS at app level
    Talisman(
        app,
        force_https=False,  # Render handles SSL termination
        strict_transport_security=not FLASK_DEBUG,
        session_cookie_secure=not FLASK_DEBUG,
        content_security_policy={
            'default-src': "'self'",
            'script-src': "'self' 'unsafe-inline'",
            'style-src': "'self' 'unsafe-inline'",
            'img-src': "'self' data:",
            'font-src': "'self'",
        } if not FLASK_DEBUG else None
    )

    from app.blueprints.auth import auth_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.user import user_bp
    from app.blueprints.main import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(main_bp)

    if not app.debug:
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / 'app.log',
            maxBytes=10240,
            backupCount=10
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('GL_JB startup')

    with app.app_context():
        try:
            db.create_all()
            app.logger.info('Database tables created/verified')
        except Exception as e:
            # Multiple gunicorn workers may race to create tables — benign
            app.logger.info(f'Database tables already exist ({type(e).__name__})')
        try:
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            app.logger.info(f'Tables in database: {tables}')
        except Exception as e:
            app.logger.error(f'Database inspection error: {e}')

    return app