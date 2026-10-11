"""Smoke tests for /admin/analytics page."""

import pytest


class TestAnalyticsSmoke:
    def test_analytics_requires_admin(self, admin_client):
        """Страница требует админа."""
        resp = admin_client.get("/admin/analytics")
        assert resp.status_code == 200
        assert "Аналитика жалоб" in resp.get_data(as_text=True)

    def test_analytics_filters_present(self, admin_client):
        """Все фильтры присутствуют: период, курьер, статус, поиск."""
        resp = admin_client.get("/admin/analytics")
        html = resp.get_data(as_text=True)
        # Period presets
        assert "Сегодня" in html
        assert "Неделя" in html
        assert "Месяц" in html
        assert "Свой" in html
        # Courier filter
        assert "Курьер" in html
        assert "Все курьеры" in html
        # Status filter
        assert "Статус" in html
        assert "Все статусы" in html
        # Search
        assert "Поиск" in html
        assert "Номер заказа, текст жалобы, ФИО" in html

    def test_analytics_kpi_summary_cards(self, admin_client):
        """Карточки KPI-сводки рендерятся."""
        resp = admin_client.get("/admin/analytics")
        html = resp.get_data(as_text=True)
        assert "Сводка за период" in html
        assert "Всего жалоб" in html
        assert "Заказов с жалобами" in html
        assert "Всего заказов" in html
        assert "Доля жалоб" in html
        assert "Уникальных причин" in html
        assert "Курьеров с жалобами" in html

    def test_analytics_empty_state(self, admin_client):
        """При пустых данных показываются empty-state."""
        # Используем поиск, который точно не найдёт данных
        resp = admin_client.get("/admin/analytics", query_string={"search": "EMPTY_MARKER_THAT_DOES_NOT_EXIST_12345"})
        html = resp.get_data(as_text=True)
        assert "Нет данных о жалобах за выбранный период" in html
        assert "Нет данных о причинах жалоб за выбранный период" in html
        assert "Нет данных по курьерам за выбранный период" in html

    def test_analytics_search_works(self, db_session, regular_user, admin_client):
        """Поиск по жалобам работает."""
        from app.services.complaint_service import ComplaintService
        from app.models import Order

        marker = "ANALYTICS-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        order = Order(user_id=regular_user.id, order_number=marker, courier_fio="Курьер Тест")
        db_session.add(order)
        db_session.commit()
        ComplaintService.add(order, f"Причина {marker}")
        db_session.commit()

        resp = admin_client.get("/admin/analytics", query_string={"search": marker})
        html = resp.get_data(as_text=True)
        assert marker in html

    def test_analytics_courier_filter_works(self, db_session, regular_user, admin_client):
        """Фильтр по курьеру работает."""
        from app.services.complaint_service import ComplaintService
        from app.models import Order

        marker = "ANALYTICS-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        courier = f"Курьер {marker}"
        order1 = Order(user_id=regular_user.id, order_number=f"{marker}-1", courier_fio=courier)
        order2 = Order(user_id=regular_user.id, order_number=f"{marker}-2", courier_fio="Другой курьер")
        db_session.add_all([order1, order2])
        db_session.commit()
        ComplaintService.add(order1, f"Жалоба {marker}")
        db_session.commit()

        resp = admin_client.get("/admin/analytics", query_string={"courier": courier})
        html = resp.get_data(as_text=True)
        assert courier in html

    def test_analytics_status_filter_works(self, db_session, regular_user, admin_client):
        """Фильтр по статусу работает."""
        from app.services.complaint_service import ComplaintService
        from app.models import Order

        marker = "ANALYTICS-SMOKE-" + __import__("uuid").uuid4().hex[:8]
        order = Order(user_id=regular_user.id, order_number=marker)
        db_session.add(order)
        db_session.commit()
        ComplaintService.add(order, f"Жалоба {marker}", status="открыта")
        db_session.commit()

        resp = admin_client.get("/admin/analytics", query_string={"status": "открыта"})
        html = resp.get_data(as_text=True)
        assert "открыта" in html

    def test_analytics_methodology_section(self, admin_client):
        """Раздел методологии присутствует."""
        resp = admin_client.get("/admin/analytics")
        html = resp.get_data(as_text=True)
        assert "Методология и определения" in html
        assert "корреляция" in html.lower()
        assert "≠" in html or "не означает" in html.lower()

    def test_analytics_period_presets_links(self, admin_client):
        """Ссылки пресетов периода содержат правильные параметры."""
        resp = admin_client.get("/admin/analytics")
        html = resp.get_data(as_text=True)
        assert 'period=today' in html
        assert 'period=week' in html
        assert 'period=month' in html
        assert 'period=custom' in html