import secrets
import string

from app.models import User


def generate_token() -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        token = "".join(secrets.choice(alphabet) for _ in range(5))
        if not User.query.filter_by(token=token).first():
            return token
