from typing import Optional, List, Tuple
from sqlalchemy import or_, func
from sqlalchemy.orm import joinedload, selectinload
import time
from app.models.order import Order
from app.models.user import User
from app.models.photo import Photo
from app.extensions import db
from app.services.user_service import UserService

# Simple in-memory cache for dashboard stats
_stats_cache = {'data': None, 'ts': 0}
_STATS_TTL = 60  # seconds


class OrderService:
    @staticmethod
    def create_order(user_id: int, order_number: str, description: str, external_id: str = None) -> Order:
        order = Order(
            user_id=user_id,
            order_number=order_number.strip(),
            description=description.strip() if description else '',
            external_id=external_id
        )
        db.session.add(order)
        db.session.commit()
        OrderService.invalidate_stats_cache()
        return order

    @staticmethod
    def get_order_by_id(order_id: int) -> Optional[Order]:
        return db.session.get(Order, order_id)

    @staticmethod
    def get_order_by_external_id(external_id: str) -> Optional[Order]:
        return Order.query.filter_by(external_id=external_id).first()

    @staticmethod
    def get_user_orders(user_id: int, page: int = 1, per_page: int = 10, search: str = '') -> object:
        query = Order.query.filter_by(user_id=user_id)
        if search:
            query = query.filter(
                or_(
                    Order.order_number.ilike(f'%{search}%'),
                    Order.description.ilike(f'%{search}%')
                )
            )
        return query.order_by(Order.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

    @staticmethod
    def get_all_orders(page: int = 1, per_page: int = 20, search: str = '', user_filter: str = '', photo_filter: str = '') -> object:
        query = db.session.query(Order, User).join(User, Order.user_id == User.id)

        if search:
            query = query.filter(
                or_(
                    Order.order_number.ilike(f'%{search}%'),
                    Order.description.ilike(f'%{search}%'),
                    User.fio.ilike(f'%{search}%')
                )
            )

        if user_filter:
            query = query.filter(User.id == user_filter)

        if photo_filter == 'with_photos':
            query = query.filter(Order.photos.any())
        elif photo_filter == 'without_photos':
            query = query.filter(~Order.photos.any())

        # Pre-fetch photo counts to avoid N+1
        photo_counts = dict(
            db.session.query(Photo.order_id, func.count(Photo.id))
            .group_by(Photo.order_id)
            .all()
        )

        pagination = query.order_by(Order.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        orders_with_users = []
        for order, user in pagination.items:
            orders_with_users.append({
                'order': order,
                'user': user,
                'photos_count': photo_counts.get(order.id, 0)
            })

        return pagination, orders_with_users

    @staticmethod
    def update_order(order_id: int, order_number: str, description: str) -> Optional[Order]:
        order = db.session.get(Order, order_id)
        if order:
            order.order_number = order_number.strip()
            order.description = description.strip()
            db.session.commit()
        return order

    @staticmethod
    def delete_order(order_id: int) -> bool:
        order = db.session.get(Order, order_id)
        if order:
            db.session.delete(order)
            db.session.commit()
            OrderService.invalidate_stats_cache()
            return True
        return False

    @staticmethod
    def get_dashboard_stats() -> dict:
        now = time.time()
        if _stats_cache['data'] and (now - _stats_cache['ts']) < _STATS_TTL:
            return _stats_cache['data']

        users_count = User.query.count()
        orders_count = Order.query.count()
        photos_count = Photo.query.count()
        orders_with_photos = db.session.query(Order.id).join(Photo, Order.id == Photo.order_id).distinct().count()
        result = {
            'users_count': users_count,
            'orders_count': orders_count,
            'photos_count': photos_count,
            'orders_with_photos': orders_with_photos
        }
        _stats_cache['data'] = result
        _stats_cache['ts'] = now
        return result

    @staticmethod
    def invalidate_stats_cache():
        _stats_cache['data'] = None
        _stats_cache['ts'] = 0

    @staticmethod
    def get_order_with_user(order_id: int) -> Optional[Tuple[Order, User]]:
        return db.session.query(Order, User).join(User).filter(Order.id == order_id).first()

    @staticmethod
    def get_user_order(order_id: int, user_id: int) -> Optional[Order]:
        return Order.query.filter_by(id=order_id, user_id=user_id).first()