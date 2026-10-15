import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# Секреты — ОБЯЗАТЕЛЬНО задайте в .env (без дефолтов!)
_SECRET_KEY = os.environ.get("SECRET_KEY")
if not _SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set in .env")
SECRET_KEY: str = _SECRET_KEY

# Пароль администратора — ОБЯЗАТЕЛЬНО задайте в .env (нет дефолта!)
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not _ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD must be set in .env")
ADMIN_PASSWORD: str = _ADMIN_PASSWORD

# Токен суперадмина — ОБЯЗАТЕЛЬНО задайте в .env (нет дефолта!)
_ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
if not _ADMIN_TOKEN:
    raise RuntimeError("ADMIN_TOKEN must be set in .env")
ADMIN_TOKEN: str = _ADMIN_TOKEN

DATABASE_URL = os.environ.get("DATABASE_URL", f'sqlite:///{BASE_DIR / "database.db"}')
DATABASE_PATH = str(BASE_DIR / "database.db")

# Путь к загрузкам обязан быть абсолютным: Flask отдаёт файлы от app.root_path,
# относительный путь ("uploads") давал 404 при отдаче и путаницу при записи.
_upload_folder = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
UPLOAD_FOLDER = _upload_folder if os.path.isabs(_upload_folder) else str(BASE_DIR / _upload_folder)
MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# Еженедельный рейтинг курьеров (§5-§6 брифа).
# Пороги ПРЕДЛОЖЕНЫ и требуют согласования до production:
#   RATING_MIN_ORDERS — минимальная выборка заказов за неделю для расчёта балла;
#   RATING_PENALTY_PER_PERCENT — вес штрафа K в формуле 100 - rate * K;
#   RATING_DATE_SOURCE — источник даты заказа для недели:
#     "delivered_at" (дата доставки, по умолчанию) | "created_at" (дата создания).
RATING_MIN_ORDERS = int(os.environ.get("RATING_MIN_ORDERS", 10))
RATING_PENALTY_PER_PERCENT = float(os.environ.get("RATING_PENALTY_PER_PERCENT", 1.0))
RATING_DATE_SOURCE = os.environ.get("RATING_DATE_SOURCE", "delivered_at")

RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "memory://")

FLASK_ENV = os.environ.get("FLASK_ENV", "development")
# Безопасный по умолчанию режим: debug включается только явно (FLASK_DEBUG=1
# в .env для разработки). Раньше дефолт был "1" — деплой без переменной
# окружения поднимал приложение с debug (отключённые CSP/HSTS/Secure-куки).
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

# Базовый флаг Secure-куки. Фактическое значение пересчитывается на каждый запрос
# (см. app/__init__.py): Secure-куку нельзя ставить на чистом HTTP, иначе браузер
# её отбрасывает и пользователь «выпадает» из аккаунта сразу после логина.
SESSION_COOKIE_SECURE = not FLASK_DEBUG

# Срок жизни запомнённого входа («вход по тегу»). Без permanent-куки сессия —
# browser-session cookie: закрыл браузер/PWA — вход потерян. Теперь при логине
# ставится session.permanent=True и кука получает Expires (30 дней по умолчанию,
# настраивается SESSION_LIFETIME_DAYS в .env). Роль перечитывается из БД на
# каждый запрос (admin_required/user_required), отзыв роли по-прежнему сбрасывает сессию.
SESSION_LIFETIME_DAYS = int(os.environ.get("SESSION_LIFETIME_DAYS", 30))
PERMANENT_SESSION_LIFETIME = timedelta(days=SESSION_LIFETIME_DAYS)

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
