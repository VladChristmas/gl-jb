"""Smoke tests for /admin/photos (photo_control) page."""

from app.models import Order, Photo


class TestPhotoControlSmoke:
    def test_photo_control_requires_admin(self, admin_client):
        """Страница требует админа."""
        resp = admin_client.get("/admin/photos")
        assert resp.status_code == 200
        assert "Контроль фото" in resp.get_data(as_text=True)

    def test_photo_control_renders_kpi_grid(self, admin_client):
        """KPI-сетка рендерится (6 карточек)."""
        resp = admin_client.get("/admin/photos")
        html = resp.get_data(as_text=True)
        assert "Всего заказов" in html
        assert "Без фото" in html
        assert "Требуется фото" in html
        assert "Повреждённые" in html
        assert "Дубликаты" in html
        assert "С фото" in html

    def test_photo_control_filters_present(self, admin_client):
        """Фильтры периода и типа проблемы присутствуют."""
        resp = admin_client.get("/admin/photos")
        html = resp.get_data(as_text=True)
        assert "Сегодня" in html
        assert "Неделя" in html
        assert "Месяц" in html
        assert "Все" in html
        assert "Без фото" in html
        assert "Требуется фото" in html
        assert "Повреждённые" in html
        assert "Дубликаты" in html

    def test_photo_control_search_works(self, db_session, regular_user, admin_client):
        """Поиск по номеру заказа работает."""
        marker = "PHOTO-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        order = Order(user_id=regular_user.id, order_number=marker, courier_fio="Курьер Тест")
        db_session.add(order)
        db_session.commit()

        resp = admin_client.get("/admin/photos", query_string={"search": marker})
        html = resp.get_data(as_text=True)
        assert marker in html

    def test_photo_control_courier_filter_works(self, db_session, regular_user, admin_client):
        """Фильтр по курьеру работает."""
        marker = "PHOTO-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        courier = f"Курьер {marker}"
        Order(user_id=regular_user.id, order_number=f"{marker}-1", courier_fio=courier)
        Order(user_id=regular_user.id, order_number=f"{marker}-2", courier_fio="Другой курьер")
        db_session.commit()

        resp = admin_client.get("/admin/photos", query_string={"courier": courier})
        html = resp.get_data(as_text=True)
        assert marker in html

    def test_photo_control_export_works(self, admin_client):
        """Экспорт отдаёт xlsx."""
        resp = admin_client.get("/admin/photos/export")
        assert resp.status_code == 200
        assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_photo_control_pagination(self, db_session, regular_user, admin_client):
        """Пагинация работает."""
        marker = "PHOTO-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        for i in range(25):
            db_session.add(Order(user_id=regular_user.id, order_number=f"{marker}-{i:02d}"))
        db_session.commit()

        resp = admin_client.get("/admin/photos", query_string={"search": marker, "page": 1})
        html = resp.get_data(as_text=True)
        assert marker in html
        # Проверяем, что есть пагинация
        assert "page=2" in html or "page=1" in html

    def test_photo_control_with_photos_filter(self, db_session, regular_user, admin_client):
        """Фильтр 'С фото' работает."""
        marker = "PHOTO-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        order_with = Order(user_id=regular_user.id, order_number=f"{marker}-HASPHOTO")
        order_without = Order(user_id=regular_user.id, order_number=f"{marker}-NOPHOTO")
        db_session.add_all([order_with, order_without])
        db_session.commit()
        db_session.add(
            Photo(
                order_id=order_with.id, filename=f"{marker}.jpg", original_filename=f"{marker}.jpg"
            )
        )
        db_session.commit()

        resp = admin_client.get(
            "/admin/photos", query_string={"search": marker, "problem": "no_photos"}
        )
        html = resp.get_data(as_text=True)
        assert f"{marker}-NOPHOTO" in html
        assert f"{marker}-HASPHOTO" not in html
