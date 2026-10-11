"""Regression: загрузка фото не должна ждать SMTP (P1-5).

Раньше `EmailService.send_order_completion_email` вызывался синхронно в
POST-запросе — холодный SMTP держал ответ десятки секунд, а при недоступной
почте пользователь вообще получал ошибку вместо «Фото загружено».
"""

import io
import threading
from unittest.mock import patch

import pytest

from app.models import Photo
from app.services.email_service import EmailService
from app.services.photo_service import PhotoService

JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01])


@pytest.fixture(autouse=True)
def _remove_uploaded_photos(app, db_session):
    """Тесты загружают фото в общую (session-scoped) БД.

    Без очистки заказ с фото переживает модуль и ломает
    `test_order_service.test_get_all_orders`, который проверяет, что заказ
    без фото не попадает в выдачу.
    """
    before = {photo.id for photo in Photo.query.all()}
    yield
    for photo_id in {photo.id for photo in Photo.query.all()} - before:
        photo = db_session.get(Photo, photo_id)
        if photo is not None:
            PhotoService.delete_photo_admin(photo_id, photo.order_id)


def _upload(auth_client, order_id: int):
    return auth_client.post(
        f"/orders/{order_id}/upload",
        data={"photo": (io.BytesIO(JPEG + b"async email"), "photo.jpg")},
        content_type="multipart/form-data",
    )


def test_upload_queues_email_instead_of_sending_it_inline(auth_client, sample_order, regular_user):
    with (
        patch.object(EmailService, "dispatch_order_completion_email") as dispatch_mock,
        patch.object(EmailService, "send_order_completion_email") as sync_mock,
    ):
        resp = _upload(auth_client, sample_order.id)

    assert resp.status_code == 302
    dispatch_mock.assert_called_once()
    sync_mock.assert_not_called()


def test_dispatch_runs_in_background_thread_without_celery(app, sample_order, regular_user):
    sent = threading.Event()

    def _fake_send(order, user):
        sent.set()
        return True

    with app.app_context():
        assert getattr(app, "celery", None) is None, "в тестах Celery не должен быть настроен"
        # Ждём внутри patch-контекста: поток обращается к классу EmailService
        with patch.object(EmailService, "send_order_completion_email", side_effect=_fake_send):
            EmailService.dispatch_order_completion_email(sample_order, regular_user)
            assert sent.wait(timeout=10), "письмо не ушло в фон"


def test_upload_succeeds_even_if_email_dispatch_fails(auth_client, sample_order, regular_user):
    with patch.object(
        EmailService, "dispatch_order_completion_email", side_effect=RuntimeError("broker down")
    ):
        resp = _upload(auth_client, sample_order.id)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/orders")
