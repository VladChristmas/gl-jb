import pytest
from app.services.order_service import OrderService
from app.models import Order, User


class TestOrderService:
    def test_create_order(self, db_session, regular_user):
        order = OrderService.create_order(
            user_id=regular_user.id,
            order_number='TEST-001',
            description='Test order'
        )
        assert order.order_number == 'TEST-001'
        assert order.description == 'Test order'
        assert order.user_id == regular_user.id
        assert order.id is not None

    def test_get_order_by_id(self, db_session, sample_order):
        found = OrderService.get_order_by_id(sample_order.id)
        assert found is not None
        assert found.order_number == sample_order.order_number

    def test_get_user_orders(self, db_session, regular_user, sample_order):
        pagination = OrderService.get_user_orders(regular_user.id, page=1, per_page=10)
        assert pagination.total == 1
        assert pagination.items[0].order_number == 'TEST-001'

    def test_get_all_orders(self, db_session, regular_user, sample_order):
        pagination, orders = OrderService.get_all_orders(page=1, per_page=10)
        assert pagination.total >= 1
        assert len(orders) >= 1

    def test_update_order(self, db_session, sample_order):
        updated = OrderService.update_order(
            sample_order.id,
            order_number='UPDATED-001',
            description='Updated description'
        )
        assert updated is not None
        assert updated.order_number == 'UPDATED-001'
        assert updated.description == 'Updated description'

    def test_delete_order(self, db_session, sample_order):
        order_id = sample_order.id
        result = OrderService.delete_order(order_id)
        assert result is True
        
        found = OrderService.get_order_by_id(order_id)
        assert found is None

    def test_get_dashboard_stats(self, db_session, regular_user, sample_order):
        stats = OrderService.get_dashboard_stats()
        assert 'users_count' in stats
        assert 'orders_count' in stats
        assert 'photos_count' in stats
        assert 'orders_with_photos' in stats
        assert stats['orders_count'] >= 1