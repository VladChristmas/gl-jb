"""Regression: лимиты на тяжёлые и дорогие POST-эндпоинты (P1-9).

Раньше лимитов на загрузку фото и импорт Excel не было вообще —
а `memory://` при нескольких gunicorn-воркерах считает каждый своё.
"""

import pytest

from app.extensions import limiter
from app.models import Order


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


class TestUploadRateLimit:
    def test_photo_upload_is_rate_limited(self, auth_client, db_session, regular_user):
        order = Order(user_id=regular_user.id, order_number="RL-1")
        db_session.add(order)
        db_session.commit()

        statuses = [
            auth_client.post(f"/orders/{order.id}/upload", data={}).status_code for _ in range(31)
        ]
        assert statuses[0] != 429, "лимит срабатывает раньше 30-го запроса"
        assert statuses[-1] == 429, f"31-й запрос должен получить 429: {statuses[-4:]}"

    def test_admin_excel_upload_is_rate_limited(self, admin_client):
        statuses = [
            admin_client.post("/admin/upload_excel", data={}).status_code for _ in range(11)
        ]
        assert statuses[0] != 429, "лимит срабатывает раньше 10-го запроса"
        assert statuses[-1] == 429, f"11-й запрос должен получить 429: {statuses[-4:]}"

    def test_admin_import_excel_is_rate_limited(self, admin_client):
        statuses = [
            admin_client.post("/admin/import_excel", data={}).status_code for _ in range(11)
        ]
        assert statuses[0] != 429, "лимит срабатывает раньше 10-го запроса"
        assert statuses[-1] == 429, f"11-й запрос должен получить 429: {statuses[-4:]}"


class TestAdminApiRateLimit:
    """P2-9: раньше обе API были полностью exempt — форк-бомба молотила БД."""

    def test_admin_api_files_is_rate_limited(self, admin_client):
        statuses = [admin_client.get("/admin/api/files").status_code for _ in range(61)]
        assert statuses[59] != 429, "лимит срабатывает раньше 60-го запроса"
        assert statuses[-1] == 429, f"61-й запрос должен получить 429: {statuses[-4:]}"

    def test_admin_api_pending_count_is_rate_limited(self, admin_client):
        statuses = [admin_client.get("/admin/api/pending_count").status_code for _ in range(121)]
        assert statuses[119] != 429, "лимит срабатывает раньше 120-го запроса"
        assert statuses[-1] == 429, f"121-й запрос должен получить 429: {statuses[-4:]}"


class TestRateLimitDocumentation:
    def test_env_example_documents_redis_for_multiple_workers(self):
        from pathlib import Path

        example = Path(__file__).resolve().parents[1] / ".env.example"
        content = example.read_text(encoding="utf-8")
        assert "redis://" in content
        assert "воркере" in content or "workers" in content
