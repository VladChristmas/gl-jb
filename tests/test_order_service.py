from app.models.order import Order
from app.models.photo import Photo
from app.services.order_service import OrderService, split_description


class TestOrderService:
    def test_create_order(self, db_session, regular_user):
        order = OrderService.create_order(
            user_id=regular_user.id, order_number="TEST-001", description="Test order"
        )
        assert order.order_number == "TEST-001"
        assert order.description == "Test order"
        assert order.user_id == regular_user.id
        assert order.id is not None

    def test_get_order_by_id(self, db_session, sample_order):
        found = OrderService.get_order_by_id(sample_order.id)
        assert found is not None
        assert found.order_number == sample_order.order_number

    def test_get_user_orders(self, db_session, regular_user, sample_order):
        pagination = OrderService.get_user_orders(regular_user.id, page=1, per_page=10)
        assert pagination.total == 1
        assert pagination.items[0].order_number == "TEST-001"

    def test_get_all_orders(self, db_session, regular_user, sample_order):
        # §4: по умолчанию список показывает ВСЕ заказы — как и KPI дашборда
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="VISIBLE-1",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()
        # per_page с запасом: session-scoped in-memory БД накапливает заказы
        # из других тестов, при per_page=100 len(numbers) упирался в лимит.
        pagination, orders = OrderService.get_all_orders(page=1, per_page=1000)
        numbers = [item["order"].order_number for item in orders]
        assert "VISIBLE-1" in numbers
        assert "TEST-001" in numbers
        assert pagination.total == len(numbers)

    def test_get_all_orders_hides_irrelevant(self, db_session, regular_user, sample_order):
        """Заказ без фото и без фото-подтемы скрывается только в явных фильтрах;
        правило «с фото и ждущие фото» сохранено отдельным значением photo_relevant."""
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="IRRELEVANT-1",
                complaint_text="Предиктив доставка",
            )
        )
        db_session.commit()
        for photo_filter in ("photo_relevant", "with_photos", "without_photos"):
            pagination, orders = OrderService.get_all_orders(
                page=1, per_page=100, photo_filter=photo_filter
            )
            numbers = [item["order"].order_number for item in orders]
            assert "IRRELEVANT-1" not in numbers
        # правило по умолчанию (пустой фильтр) больше не прячет заказы
        pagination, orders = OrderService.get_all_orders(page=1, per_page=100)
        assert "IRRELEVANT-1" in [item["order"].order_number for item in orders]

    def test_get_all_orders_shows_with_photo(self, db_session, regular_user, sample_order):
        """Заказ с фото виден по умолчанию и в фильтре «с фото»."""
        photo = Photo(order_id=sample_order.id, filename="a.jpg", original_filename="a.jpg")
        db_session.add(photo)
        db_session.commit()
        pagination, orders = OrderService.get_all_orders(page=1, per_page=100)
        assert "TEST-001" in [item["order"].order_number for item in orders]
        pagination, orders = OrderService.get_all_orders(
            page=1, per_page=100, photo_filter="with_photos"
        )
        assert "TEST-001" in [item["order"].order_number for item in orders]
        pagination, orders = OrderService.get_all_orders(
            page=1, per_page=100, photo_filter="without_photos"
        )
        assert "TEST-001" not in [item["order"].order_number for item in orders]

    def test_update_order(self, db_session, sample_order):
        updated = OrderService.update_order(
            sample_order.id, order_number="UPDATED-001", description="Updated description"
        )
        assert updated is not None
        assert updated.order_number == "UPDATED-001"
        assert updated.description == "Updated description"

    def test_delete_order(self, db_session, sample_order):
        order_id = sample_order.id
        result = OrderService.delete_order(order_id)
        assert result is True

        found = OrderService.get_order_by_id(order_id)
        assert found is None

    def test_get_dashboard_stats(self, db_session, regular_user, sample_order):
        stats = OrderService.get_dashboard_stats()
        assert "users_count" in stats
        assert "orders_count" in stats
        assert "photos_count" in stats
        assert "orders_with_photos" in stats
        assert stats["orders_count"] >= 1


class TestSplitDescription:
    """F-05 UX-аудита: описание заказа = человеческая часть + служебные поля."""

    def test_legacy_technical_fields_are_separated(self):
        desc = "czf: МОК Бишкек 14:1; confirmed_at: 3.10.26 08:00; sla: 15; sum: 1777"
        human, technical = split_description(desc)
        assert human == ""
        assert "czf: МОК Бишкек 14:1" in technical
        assert "sla: 15" in technical
        assert "sum: 1777" in technical

    def test_human_labels_stay_human(self):
        desc = "Адрес: Бишкек, Ленина 1\nПодтема: нужно фото\nДата: 01.10.2026"
        human, technical = split_description(desc)
        assert "Адрес: Бишкек, Ленина 1" in human
        assert "Подтема: нужно фото" in human
        assert technical == ""

    def test_mixed_description_is_split(self):
        desc = "Адрес: Ош, Масалиева 5\nczf: 42; sla: 10"
        human, technical = split_description(desc)
        assert "Адрес: Ош, Масалиева 5" in human
        assert "czf: 42" in technical
        assert "czf" not in human

    def test_empty_description(self):
        assert split_description(None) == ("", "")
        assert split_description("") == ("", "")

    def test_summary_prefers_address(self, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="SUM-1",
            address="Бишкек, ул. Мира 7",
            description="czf: X; sla: 5",
        )
        db_session.add(order)
        db_session.commit()
        assert OrderService.order_summary(order) == "Бишкек, ул. Мира 7"

    def test_summary_falls_back_to_human_part(self, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="SUM-2",
            description="Адрес: Бишкек, Южная 3\nczf: Y",
        )
        db_session.add(order)
        db_session.commit()
        assert OrderService.order_summary(order) == "Адрес: Бишкек, Южная 3"

    def test_summary_empty_for_technical_only(self, db_session, regular_user):
        order = Order(user_id=regular_user.id, order_number="SUM-3", description="czf: Z; sla: 1")
        db_session.add(order)
        db_session.commit()
        assert OrderService.order_summary(order) == ""

    def test_summary_none_order(self):
        assert OrderService.order_summary(None) == ""
