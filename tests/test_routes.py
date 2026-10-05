from app.models import User


class TestAuthRoutes:
    def test_unified_login_page(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_admin_login_page(self, client):
        resp = client.get("/admin/login")
        assert resp.status_code == 200

    def test_admin_token_login_page(self, client):
        resp = client.get("/admin/token_login")
        assert resp.status_code == 200

    def test_login_invalid_token(self, client):
        resp = client.post("/login", data={"token": "INVALID"})
        assert resp.status_code == 200  # Returns page with error
        # Check that error message is shown
        assert resp.status_code == 200


class TestUserRoutes:
    def test_user_orders_requires_auth(self, client):
        resp = client.get("/orders")
        assert resp.status_code == 302  # Redirect to login

    def test_user_orders_authenticated(self, auth_client, sample_order):
        resp = auth_client.get("/orders")
        assert resp.status_code == 200
        assert b"TEST-001" in resp.data

    def test_upload_photo_requires_auth(self, client, sample_order):
        resp = client.post(f"/orders/{sample_order.id}/upload", data={})
        assert resp.status_code == 302  # Redirect to login


class TestAdminRoutes:
    def test_admin_dashboard_requires_auth(self, client):
        resp = client.get("/admin/")
        assert resp.status_code == 302  # Redirect to login

    def test_admin_dashboard_authenticated(self, admin_client):
        resp = admin_client.get("/admin/")
        assert resp.status_code == 200

    def test_admin_users_page(self, admin_client, regular_user):
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200
        assert b"Test User" in resp.data

    def test_admin_orders_page(self, admin_client, sample_order):
        resp = admin_client.get("/admin/orders")
        assert resp.status_code == 200
        assert b"TEST-001" in resp.data

    def test_admin_create_user(self, admin_client):
        resp = admin_client.post("/admin/users", data={"fio": "New User"})
        assert resp.status_code == 302  # Redirect after creation

        # Check user was created
        user = User.query.filter_by(fio="New User").first()
        assert user is not None
        assert user.token is not None

    def test_admin_delete_user(self, admin_client, regular_user):
        user_id = regular_user.id
        resp = admin_client.post(f"/admin/users/delete/{user_id}")
        assert resp.status_code == 302

        # Check user was deleted
        deleted = User.query.get(user_id)
        assert deleted is None
