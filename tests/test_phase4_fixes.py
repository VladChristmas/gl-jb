import io
import os

from werkzeug.datastructures import FileStorage

from app.constants import ROLE_ADMIN, ROLE_COURIER, ROLE_PICKER
from app.services.dashboard_service import DashboardService
from app.services.photo_service import PhotoService
from config import ADMIN_TOKEN, UPLOAD_FOLDER

# Минимальный JPEG-заголовок (та же конструкция, что в test_photo_service.py)
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01])


def _make_jpeg(name: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(JPEG + b"fake image content"), filename=name)


class TestUserIdValidation:
    """Фильтр user_id раньше ронял /admin/orders с HTTP 500 на нечисловом значении."""

    def test_orders_page_non_numeric_user_id(self, admin_client):
        resp = admin_client.get("/admin/orders?user_id=abc")
        assert resp.status_code == 200

    def test_orders_page_numeric_user_id(self, admin_client, regular_user):
        resp = admin_client.get(f"/admin/orders?user_id={regular_user.id}")
        assert resp.status_code == 200


class TestUploadsAuth:
    """/uploads раньше отдавал файлы любому анониму."""

    def _upload_photo(self, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(_make_jpeg("test.jpg"), sample_order.id, regular_user.id)
        assert photo is not None
        return photo

    def test_anonymous_get_is_denied(self, client, db_session, sample_order, regular_user):
        photo = self._upload_photo(db_session, sample_order, regular_user)
        try:
            resp = client.get(f"/uploads/{photo.filename}")
            assert resp.status_code == 401
        finally:
            PhotoService.delete_photo_admin(photo.id, sample_order.id)

    def test_admin_session_can_get(self, admin_client, db_session, sample_order, regular_user):
        photo = self._upload_photo(db_session, sample_order, regular_user)
        try:
            resp = admin_client.get(f"/uploads/{photo.filename}")
            assert resp.status_code == 200
        finally:
            PhotoService.delete_photo_admin(photo.id, sample_order.id)

    def test_admin_token_header_can_get(self, client, db_session, sample_order, regular_user):
        photo = self._upload_photo(db_session, sample_order, regular_user)
        try:
            resp = client.get(f"/uploads/{photo.filename}", headers={"X-Admin-Token": ADMIN_TOKEN})
            assert resp.status_code == 200
        finally:
            PhotoService.delete_photo_admin(photo.id, sample_order.id)


class TestAdminPhotoRoutes:
    """Админ раньше попадал на курьерский роут фото → 302 /login."""

    def test_admin_opens_order_photos(self, admin_client, sample_order):
        resp = admin_client.get(f"/admin/orders/{sample_order.id}/photos")
        assert resp.status_code == 200

    def test_anonymous_redirected_from_admin_photos(self, client, sample_order):
        resp = client.get(f"/admin/orders/{sample_order.id}/photos")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_admin_deletes_photo(self, admin_client, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(_make_jpeg("test.jpg"), sample_order.id, regular_user.id)
        assert photo is not None
        resp = admin_client.post(
            f"/admin/orders/{sample_order.id}/photos/delete/{photo.id}",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert PhotoService.get_order_photos(sample_order.id) == []
        assert not os.path.exists(os.path.join(UPLOAD_FOLDER, photo.filename))


class TestXlsRejected:
    """openpyxl не умеет .xls — раньше файл принимали и падали с невнятной ошибкой."""

    def test_upload_excel_rejects_xls(self, admin_client):
        resp = admin_client.post(
            "/admin/upload_excel",
            data={"excel_file": (io.BytesIO(b"old xls"), "orders.xls")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        text = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert ".xls" in text and ".xlsx" in text

    def test_upload_excel_rejects_unknown_extension(self, admin_client):
        resp = admin_client.post(
            "/admin/upload_excel",
            data={"excel_file": (io.BytesIO(b"data"), "orders.csv")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert ".xlsx" in resp.get_data(as_text=True)


class TestCouriersCount:
    """Карточка «Курьеров» раньше показывала всех пользователей (включая сборщиков/админов)."""

    def test_counts_only_courier_role(self, app, db_session):
        from app.services.user_service import UserService

        before = DashboardService.admin_stats()["couriers_count"]
        users_before = DashboardService.admin_stats()["users_count"]

        UserService.create_user("Фикс Курьер", role=ROLE_COURIER)
        UserService.create_user("Фикс Сборщик", role=ROLE_PICKER)
        UserService.create_user("Фикс Админ", role=ROLE_ADMIN)

        stats = DashboardService.admin_stats()
        # «Курьеров» растёт ровно на 1 (только роль courier), «Пользователей» — на 3
        assert stats["couriers_count"] - before == 1
        assert stats["users_count"] - users_before == 3


class TestCyrillicFilenameUpload:
    """secure_filename('фото.jpg') == 'jpg' — раньше ext.rsplit('.', 1)[1] давал IndexError."""

    def test_save_photo_with_cyrillic_filename(self, db_session, sample_order, regular_user):
        photo = PhotoService.save_photo(_make_jpeg("фото.jpg"), sample_order.id, regular_user.id)
        try:
            assert photo is not None
            assert photo.filename.endswith(".jpg")
            assert os.path.exists(os.path.join(UPLOAD_FOLDER, photo.filename))
            assert photo.original_filename == "фото.jpg"
        finally:
            if photo:
                PhotoService.delete_photo_admin(photo.id, sample_order.id)
