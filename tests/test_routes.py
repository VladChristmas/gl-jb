from app.models import Order, User


class TestAuthRoutes:
    def test_unified_login_page(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_root_redirects_anonymous_to_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_root_redirects_admin_to_admin_dashboard(self, admin_client):
        """Логотип (brand -> main.index) не должен разлогинивать админа."""
        resp = admin_client.get("/")
        assert resp.status_code == 302
        assert "/admin/" in resp.headers["Location"]
        # сессия сохранена: dashboard доступен сразу после перехода
        dash = admin_client.get("/admin/")
        assert dash.status_code == 200

    def test_root_redirects_user_to_user_dashboard(self, auth_client):
        """Логотип (brand -> main.index) не должен разлогинивать пользователя."""
        resp = auth_client.get("/")
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["Location"]
        # сессия сохранена: dashboard доступен сразу после перехода
        dash = auth_client.get("/dashboard")
        assert dash.status_code == 200

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

    def test_user_orders_shows_address_and_photo_hint(self, auth_client, db_session, regular_user):
        """Этап 7: адрес доставки и подсказка «нужно фото по жалобе»."""
        order = Order(
            user_id=regular_user.id,
            order_number="ADDR-1",
            address="ул. Пушкина, д. 10",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order)
        db_session.commit()

        html = auth_client.get("/orders").get_data(as_text=True)
        assert "ул. Пушкина, д. 10" in html
        assert "обязательно фото-подтверждение" in html
        # Фильтр-чип «Жрут фото» со счётчиком (§4)
        assert "photo=pending" in html
        assert "Ждут фото" in html

    def test_user_orders_pending_filter(self, auth_client, db_session, regular_user):
        """Фильтр photo=pending: только заказы с фото-подтемой без фото."""
        waiting = Order(
            user_id=regular_user.id,
            order_number="PEND-1",
            complaint_text="Принесли чужой заказ",
        )
        done = Order(
            user_id=regular_user.id,
            order_number="DONE-1",
            complaint_text="Не донесли часть товаров из заказа",
        )
        from app.models import Photo

        db_session.add(waiting)
        db_session.add(done)
        db_session.flush()
        db_session.add(Photo(order_id=done.id, filename="x.jpg", original_filename="x.jpg"))
        db_session.commit()

        html = auth_client.get("/orders?photo=pending").get_data(as_text=True)
        assert "PEND-1" in html
        assert "DONE-1" not in html

        # Неизвестное значение фильтра игнорируется (без 500)
        resp = auth_client.get("/orders?photo=хак")
        assert resp.status_code == 200
        assert "PEND-1" in resp.get_data(as_text=True)

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

    def test_admin_orders_page(self, admin_client, db_session, regular_user, sample_order):
        db_session.add_all(
            [
                Order(
                    user_id=regular_user.id,
                    order_number="VISIBLE-R",
                    complaint_text="Товар побит/вскрыт",
                ),
                Order(
                    user_id=regular_user.id,
                    order_number="IRRELEVANT-R",
                    complaint_text="Предиктив доставка",
                ),
            ]
        )
        db_session.commit()
        resp = admin_client.get("/admin/orders")
        assert resp.status_code == 200
        # §4: по умолчанию список показывает все заказы, включая KPI-выборку
        assert b"VISIBLE-R" in resp.data
        assert b"IRRELEVANT-R" in resp.data
        # старое правило «только с фото/ожидающие фото» — явным фильтром
        resp = admin_client.get("/admin/orders?photo_filter=photo_relevant")
        assert b"VISIBLE-R" in resp.data
        assert b"IRRELEVANT-R" not in resp.data

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


class TestAdminApiRoleRecheck:
    """P2: /admin/api/* перепроверяет роль администратора в БД.

    Раньше эти эндпоинты проверяли только session["is_admin"]: отзыв прав
    не действовал до истечения сессии.
    """

    def _revoked_admin_session(self, client, user):
        with client.session_transaction() as sess:
            sess["is_admin"] = True
            sess["user_id"] = user.id

    def test_pending_count_rejects_revoked_role(self, client, regular_user):
        # regular_user.role по умолчанию = courier (не админ)
        self._revoked_admin_session(client, regular_user)
        assert client.get("/admin/api/pending_count").status_code == 403
        # сессия очищена после отзыва — повторный запрос тоже отказ
        assert client.get("/admin/api/pending_count").status_code == 403

    def test_notifications_rejects_revoked_role(self, client, regular_user):
        self._revoked_admin_session(client, regular_user)
        assert client.get("/admin/api/notifications").status_code == 403

    def test_files_rejects_revoked_role(self, client, regular_user):
        self._revoked_admin_session(client, regular_user)
        assert client.get("/admin/api/files").status_code == 401

    def test_restore_rejects_revoked_role(self, client, regular_user):
        self._revoked_admin_session(client, regular_user)
        resp = client.post("/admin/api/photos/restore", json={"files": []})
        assert resp.status_code == 401

    def test_api_works_for_admin_with_db_role(self, client, admin_user, db_session):
        admin_user.role = "admin"
        db_session.commit()
        self._revoked_admin_session(client, admin_user)
        assert client.get("/admin/api/pending_count").status_code == 200
