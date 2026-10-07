import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.constants import ROLE_ADMIN, ROLE_COURIER, ROLE_PICKER
from app.models import Order, User


@pytest.fixture
def as_user(client, db_session):
    """Вход как пользователь с ролью напрямую в сессию (без POST /login)."""

    def _make(role: str, fio: str | None = None) -> User:
        unique = uuid.uuid4().hex[:8]
        user = User(
            fio=fio or f"Тест Юзер {unique}",
            token=f"T{unique}"[:10],
            role=role,
        )
        db_session.add(user)
        db_session.commit()
        with client.session_transaction() as sess:
            sess["user_id"] = user.id
            sess["user_fio"] = user.fio
        return user

    return _make


class TestCreateUserWithRole:
    def test_create_picker_user(self, admin_client, db_session):
        resp = admin_client.post(
            "/admin/users", data={"fio": "Сборщик Тест Роли", "role": ROLE_PICKER}
        )
        assert resp.status_code == 302
        user = User.query.filter_by(fio="Сборщик Тест Роли").first()
        assert user is not None
        assert user.role == ROLE_PICKER

    def test_create_admin_user(self, admin_client, db_session):
        resp = admin_client.post(
            "/admin/users", data={"fio": "Админ Тест Роли", "role": ROLE_ADMIN}
        )
        assert resp.status_code == 302
        user = User.query.filter_by(fio="Админ Тест Роли").first()
        assert user is not None
        assert user.role == ROLE_ADMIN

    def test_default_role_is_courier(self, admin_client, db_session):
        admin_client.post("/admin/users", data={"fio": "Курьер Без Роли"})
        user = User.query.filter_by(fio="Курьер Без Роли").first()
        assert user is not None
        assert user.role == ROLE_COURIER

    def test_invalid_role_falls_back_to_courier(self, admin_client, db_session):
        admin_client.post("/admin/users", data={"fio": "Хакер Роль", "role": "god"})
        user = User.query.filter_by(fio="Хакер Роль").first()
        assert user is not None
        assert user.role == ROLE_COURIER

    def test_users_page_shows_role_select_and_badges(self, admin_client, regular_user):
        resp = admin_client.get("/admin/users")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Кто это" in html
        assert 'name="role"' in html
        assert "Администратор" in html
        assert "Сборщик" in html
        assert "Роль" in html
        # роль обычного пользователя отображается бейджем
        assert regular_user.fio in html


class TestRoleChange:
    def test_change_role_to_picker(self, admin_client, regular_user, db_session):
        resp = admin_client.post(f"/admin/users/role/{regular_user.id}", data={"role": ROLE_PICKER})
        assert resp.status_code == 302
        db_session.refresh(regular_user)
        assert regular_user.role == ROLE_PICKER

    def test_invalid_role_ignored(self, admin_client, regular_user, db_session):
        db_session.refresh(regular_user)
        before = regular_user.role
        admin_client.post(f"/admin/users/role/{regular_user.id}", data={"role": "hacker"})
        db_session.refresh(regular_user)
        assert regular_user.role == before

    def test_role_change_requires_admin(self, client, regular_user, db_session):
        resp = client.post(f"/admin/users/role/{regular_user.id}", data={"role": ROLE_ADMIN})
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]
        db_session.refresh(regular_user)
        assert regular_user.role != ROLE_ADMIN


class TestRoleLogin:
    def _login(self, client, token):
        return client.post("/login", data={"token": token}, follow_redirects=False)

    def test_admin_role_user_gets_admin_session(self, client, db_session):
        user = User(fio="Роль Админ", token="RADM1", role=ROLE_ADMIN)
        db_session.add(user)
        db_session.commit()

        resp = self._login(client, "RADM1")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin/")

        resp = client.get("/admin/")
        assert resp.status_code == 200
        # и видит форму публикации в чате
        resp = client.get("/chat")
        assert "Опубликовать" in resp.get_data(as_text=True)

    def test_courier_role_user_blocked_from_admin(self, client, db_session):
        user = User(fio="Роль Курьер", token="RCOUR", role=ROLE_COURIER)
        db_session.add(user)
        db_session.commit()

        resp = self._login(client, "RCOUR")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/dashboard")

        resp = client.get("/admin/")
        assert resp.status_code == 302
        assert "/admin/login" in resp.headers["Location"]

    def test_picker_role_user_goes_to_dashboard(self, client, db_session):
        user = User(fio="Роль Сборщик", token="RPICK", role=ROLE_PICKER)
        db_session.add(user)
        db_session.commit()

        resp = self._login(client, "RPICK")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/dashboard")


class TestOrdersPageCsrf:
    def test_orders_page_has_csrf_for_upload_js(self, auth_client, sample_order):
        """Регресс: на странице /orders обязан быть csrf для JS-загрузки фото."""
        resp = auth_client.get("/orders")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'name="csrf_token"' in html
        assert 'id="upload-csrf"' in html


class TestPickerDashboard:
    def test_picker_without_data_shows_empty_state(self, client, as_user):
        as_user(ROLE_PICKER)

        resp = client.get("/dashboard")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Данных пока нет" in html
        assert "Сборщик" in html

    def test_picker_with_data_shows_stats(self, client, as_user, db_session):
        user = as_user(ROLE_PICKER)
        db_session.add(
            Order(
                user_id=user.id,
                order_number="PICK-1",
                picker_fio=user.fio,
                pick_count=42,
                wait_time="5 мин",
                pick_speed="12/час",
            )
        )
        db_session.commit()

        resp = client.get("/dashboard")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Собрано заказов" in html
        assert "12/час" in html
        assert "Мои сборки" in html
        assert "PICK-1" in html

    def test_picker_redirected_from_orders(self, client, as_user):
        as_user(ROLE_PICKER)

        resp = client.get("/orders")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/dashboard")

    def test_courier_sees_courier_dashboard(self, client, as_user):
        as_user(ROLE_COURIER)

        resp = client.get("/dashboard")
        html = resp.get_data(as_text=True)
        assert "Мои заказы" in html
        assert "Курьер" in html  # бейдж роли в шапке


class TestCourierPendingSemantics:
    def test_pending_counts_only_whitelist_topics(self, client, as_user, db_session):
        user = as_user(ROLE_COURIER)
        db_session.add_all(
            [
                Order(
                    user_id=user.id,
                    order_number="PEND-1",
                    complaint_text="Товар побит/вскрыт",
                ),
                Order(user_id=user.id, order_number="PEND-2"),
            ]
        )
        db_session.commit()

        from app.services.dashboard_service import DashboardService

        stats = DashboardService.courier_stats(user.id)
        assert stats["pending"] == 1
        assert len(stats["pending_orders"]) == 1
        assert stats["pending_orders"][0].order_number == "PEND-1"

    def test_order_without_photo_topic_is_not_pending(self, client, as_user, db_session):
        user = as_user(ROLE_COURIER)
        db_session.add(Order(user_id=user.id, order_number="PEND-3"))
        db_session.commit()

        from app.services.dashboard_service import DashboardService

        stats = DashboardService.courier_stats(user.id)
        assert stats["pending"] == 0
        assert stats["all_perfect"] is True


class TestUploadAjax:
    def test_ajax_upload_error_returns_json(self, auth_client, sample_order):
        resp = auth_client.post(
            f"/orders/{sample_order.id}/upload",
            data={},
            headers={"X-CSRFToken": "test-token"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False
        assert "Ошибка" in data["error"]

    def test_ajax_upload_success_returns_json(self, auth_client, sample_order, regular_user):
        with patch("app.blueprints.user.PhotoService.save_photo", return_value=MagicMock()):
            resp = auth_client.post(
                f"/orders/{sample_order.id}/upload",
                data={},
                headers={"X-CSRFToken": "test-token"},
            )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

    def test_form_upload_still_redirects(self, auth_client, sample_order):
        resp = auth_client.post(f"/orders/{sample_order.id}/upload", data={})
        assert resp.status_code == 302
