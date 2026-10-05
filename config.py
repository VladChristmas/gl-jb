import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

# Пароль администратора — ОБЯЗАТЕЛЬНО задайте в .env (нет дефолта!)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD must be set in .env")

# Токен суперадмина (5 символов A-Z, 0-9)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "VSE5H")

DATABASE_URL = os.environ.get("DATABASE_URL", f'sqlite:///{BASE_DIR / "database.db"}')
DATABASE_PATH = str(BASE_DIR / "database.db")

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "memory://")

FLASK_ENV = os.environ.get("FLASK_ENV", "production")
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

# Email settings (настроено для mail.ru по умолчанию)
MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.mail.ru")
MAIL_PORT = int(os.environ.get("MAIL_PORT", 465))
MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "0") == "1"
MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "1") == "1"
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
MAIL_ADMIN_RECIPIENTS = (
    os.environ.get("MAIL_ADMIN_RECIPIENTS", "").split(",")
    if os.environ.get("MAIL_ADMIN_RECIPIENTS")
    else []
)
