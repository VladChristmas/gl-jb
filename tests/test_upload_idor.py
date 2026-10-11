"""Regression: IDOR на /uploads/<filename> (P1-12).

Раньше любой залогиненный пользователь мог получить чужое фото, зная имя файла.
Теперь файл отдаётся владельцу заказа, админу или по X-Admin-Token.
"""

import io

from werkzeug.datastructures import FileStorage

from app.models import Order
from app.services.photo_service import PhotoService
from config import ADMIN_TOKEN

JPEG = bytes(
    [
        0xFF,
        0xD8,
        0xFF,
        0xE0,
        0x00,
        0x10,
        0x4A,
        0x46,
        0x49,
        0x46,
        0x00,
        0x01,
        0x01,
        0x00,
        0x00,
        0x01,
        0x00,
        0x01,
        0x00,
        0x00,
        0xFF,
        0xDB,
        0x00,
        0x43,
        0x00,
        0xFF,
        0xD9,
    ]
)


def _make_jpeg(name: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(JPEG + b"fake image content"), filename=name)


def _cleanup(photo, order_id: int) -> None:
    PhotoService.delete_photo_admin(photo.id, order_id)


class TestUploadOwnership:
    def test_owner_can_get_own_photo(self, auth_client, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(
            _make_jpeg("idor-own.jpg"), sample_order.id, regular_user.id
        )
        assert photo is not None
        try:
            resp = auth_client.get(f"/uploads/{photo.filename}")
            assert resp.status_code == 200
        finally:
            _cleanup(photo, sample_order.id)

    def test_other_user_is_forbidden(self, auth_client, db_session, admin_user):
        """auth_client вошёл как regular_user, а фото принадлежит заказу admin_user."""
        order = Order(user_id=admin_user.id, order_number="IDOR-1")
        db_session.add(order)
        db_session.commit()
        photo = PhotoService.save_photo(_make_jpeg("idor.jpg"), order.id, admin_user.id)
        assert photo is not None
        try:
            resp = auth_client.get(f"/uploads/{photo.filename}")
            assert resp.status_code == 403, "чужой пользователь получил файл (IDOR)"
            assert resp.get_json()["error"] == "forbidden"
        finally:
            _cleanup(photo, order.id)

    def test_unknown_file_is_forbidden_for_user(self, auth_client):
        resp = auth_client.get("/uploads/no-such-file.jpg")
        assert resp.status_code == 403

    def test_admin_session_can_get(self, admin_client, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(
            _make_jpeg("idor-admin.jpg"), sample_order.id, regular_user.id
        )
        assert photo is not None
        try:
            resp = admin_client.get(f"/uploads/{photo.filename}")
            assert resp.status_code == 200
        finally:
            _cleanup(photo, sample_order.id)

    def test_admin_token_still_works(self, client, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(
            _make_jpeg("idor-token.jpg"), sample_order.id, regular_user.id
        )
        assert photo is not None
        try:
            resp = client.get(f"/uploads/{photo.filename}", headers={"X-Admin-Token": ADMIN_TOKEN})
            assert resp.status_code == 200
        finally:
            _cleanup(photo, sample_order.id)

    def test_anonymous_is_unauthorized(self, client, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(
            _make_jpeg("idor-anon.jpg"), sample_order.id, regular_user.id
        )
        assert photo is not None
        try:
            resp = client.get(f"/uploads/{photo.filename}")
            assert resp.status_code == 401
        finally:
            _cleanup(photo, sample_order.id)
