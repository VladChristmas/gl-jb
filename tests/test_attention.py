"""Тесты для Центра внимания (/admin/attention) — новая функция GL_JB 2.0."""

from app.models import Complaint, Order, Photo


class TestAttentionPage:
    """Тесты страницы Центра внимания."""

    def test_attention_requires_admin(self, client):
        """Аноним перенаправляется на логин."""
        resp = client.get("/admin/attention")
        assert resp.status_code == 302

    def test_attention_accessible_by_admin(self, admin_client):
        """Админ может открыть страницу."""
        resp = admin_client.get("/admin/attention")
        assert resp.status_code == 200

    def test_attention_our_order_not_a_problem(self, admin_client, db_session, regular_user):
        """Заказ с фото и без жалоб не отображается как проблема."""
        order = Order(
            user_id=regular_user.id,
            order_number="CLEAN-1",
            description="Чистый заказ",
        )
        db_session.add(order)
        db_session.commit()
        Photo(order_id=order.id, filename="photo.jpg", original_filename="photo.jpg")
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)
        assert "CLEAN-1" not in html

        db_session.delete(order)
        db_session.commit()

    def test_attention_shows_orders_without_photos(self, admin_client, db_session, regular_user):
        """Заказы с фото-подтемой без фото отображаются как проблема."""
        order = Order(
            user_id=regular_user.id,
            order_number="NOPHOTO-1",
            description="Требуется фото",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)
        assert "Заказ без обязательных фото" in html
        assert "NOPHOTO-1" in html
        assert "требуется фото" in html

        db_session.delete(order)
        db_session.commit()

    def test_attention_shows_open_complaints(self, admin_client, db_session, regular_user):
        """Открытые жалобы отображаются как проблема."""
        order = Order(
            user_id=regular_user.id,
            order_number="COMP-1",
            description="Заказ с жалобой",
            complaint_text="Принесли чужой заказ",
        )
        db_session.add(order)
        db_session.commit()

        complaint = Complaint(
            order_id=order.id,
            text="Принесли чужой заказ",
            status="открыта",
        )
        db_session.add(complaint)
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)
        assert "Открытая жалоба" in html
        assert "COMP-1" in html
        assert "Принесли чужой заказ" in html

        db_session.delete(complaint)
        db_session.delete(order)
        db_session.commit()

    def test_attention_shows_mirror_only_complaints(self, admin_client, db_session, regular_user):
        """Жалобы только в зеркале (без строк Complaint) отображаются."""
        order = Order(
            user_id=regular_user.id,
            order_number="MIRROR-1",
            description="Зеркальная жалоба",
            complaint_text="Не донесли часть товаров из заказа",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)
        assert "Жалоба только в зеркале" in html
        assert "MIRROR-1" in html

        db_session.delete(order)
        db_session.commit()

    def test_attention_problems_sorted_by_severity(self, admin_client, db_session, regular_user):
        """Проблемы сортируются: danger → warning → info."""
        order1 = Order(
            user_id=regular_user.id,
            order_number="SEV-1",
            description="Open complaint",
            complaint_text="Принесли чужой заказ",
        )
        db_session.add(order1)
        db_session.commit()
        complaint = Complaint(
            order_id=order1.id,
            text="Принесли чужой заказ",
            status="открыта",
        )
        db_session.add(complaint)

        order2 = Order(
            user_id=regular_user.id,
            order_number="SEV-2",
            description="No photos",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order2)

        order3 = Order(
            user_id=regular_user.id,
            order_number="SEV-3",
            description="Mirror only",
            complaint_text="Не донесли часть товаров из заказа",
        )
        db_session.add(order3)
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)

        first_problem_pos = html.find("Открытая жалоба")
        warning_pos = html.find("Заказ без обязательных фото")
        info_pos = html.find("Жалоба только в зеркале")

        assert first_problem_pos < warning_pos < info_pos, (
            f"Порядок нарушен: danger={first_problem_pos}, "
            f"warning={warning_pos}, info={info_pos}"
        )

        db_session.delete(complaint)
        db_session.delete(order1)
        db_session.delete(order2)
        db_session.delete(order3)
        db_session.commit()

    def test_attention_filters_by_period(self, admin_client, db_session, regular_user):
        """Фильтр по периоду применяется к проблемам."""
        order = Order(
            user_id=regular_user.id,
            order_number="PERIOD-1",
            description="Старый заказ",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)
        assert "PERIOD-1" in html

        db_session.delete(order)
        db_session.commit()

    def test_attention_has_action_links(self, admin_client, db_session, regular_user):
        """Каждая проблема имеет ссылку на действие."""
        order = Order(
            user_id=regular_user.id,
            order_number="ACTION-1",
            description="Заказ для действия",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get("/admin/attention").get_data(as_text=True)
        assert "Открыть заказ" in html
        assert f"/admin/orders/edit/{order.id}" in html

        db_session.delete(order)
        db_session.commit()
