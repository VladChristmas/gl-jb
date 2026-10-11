import secrets

from app.models import User


def generate_token() -> str:
    """Токен доступа: `secrets.token_urlsafe(24)` = 32 символа (~192 бита).

    Раньше было 5 символов из 31-символьного алфавита (~28 бит) — подбор
    за минуты. Длина колонки `users.token` расширена до 128, старые токены
    не инвалидируются.
    """
    while True:
        token = secrets.token_urlsafe(24)
        if not User.query.filter_by(token=token).first():
            return token
