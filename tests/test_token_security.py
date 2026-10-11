"""Regression: токен доступа должен быть неуязвим к перебору (P1-8).

Раньше `generate_token()` делал 5 символов из 31-символьного алфавита
(~28 бит) — подбор за минуты, плюс `.env.example` публиковал реальный
`ADMIN_TOKEN=VSE5H`.
"""

from app.services.user_service import UserService
from app.utils.token import generate_token


class TestTokenStrength:
    def test_generated_token_is_32_chars(self, db_session):
        token = generate_token()
        assert len(token) == 32

    def test_tokens_are_unique(self, db_session):
        tokens = {generate_token() for _ in range(10)}
        assert len(tokens) == 10

    def test_token_fits_column(self, db_session):
        """Токен обязан влезать в users.token (128) — иначе INSERT на PG падает."""
        user = UserService.create_user("Токен Тест")
        assert len(user.token) <= 128

    def test_env_example_has_no_real_admin_token(self):
        from pathlib import Path

        example = Path(__file__).resolve().parents[1] / ".env.example"
        content = example.read_text(encoding="utf-8")
        assert "VSE5H" not in content
        assert "ADMIN_TOKEN=" in content
