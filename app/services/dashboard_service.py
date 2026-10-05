from typing import Optional
from datetime import datetime, timedelta
from sqlalchemy import func, distinct
from app.models.order import Order
from app.models.user import User
from app.models.photo import Photo
from app.extensions import db


class DashboardService:
    """Aggregated stats for admin and courier dashboards."""

    @staticmethod
    def admin_stats() -> dict:
        users_count = db.session.query(func.count(User.id)).scalar() or 0
        orders_count = db.session.query(func.count(Order.id)).scalar() or 0
        photos_count = db.session.query(func.count(Photo.id)).scalar() or 0

        orders_with_photos = db.session.query(
            func.count(distinct(Photo.order_id))
        ).scalar() or 0

        orders_without_photos = orders_count - orders_with_photos

        # Recent orders (last 10)
        recent_orders = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )

        # Recent photos (last 10)
        recent_photos = (
            db.session.query(Photo, Order, User)
            .join(Order, Photo.order_id == Order.id)
            .join(User, Order.user_id == User.id)
            .order_by(Photo.uploaded_at.desc())
            .limit(10)
            .all()
        )

        # Orders per day (last 7 days)
        week_ago = datetime.utcnow() - timedelta(days=7)
        daily_orders = (
            db.session.query(
                func.date(Order.created_at).label('day'),
                func.count(Order.id).label('count')
            )
            .filter(Order.created_at >= week_ago)
            .group_by(func.date(Order.created_at))
            .order_by(func.date(Order.created_at))
            .all()
        )

        # Top couriers by photo uploads
        top_couriers = (
            db.session.query(
                User.fio,
                func.count(Photo.id).label('photo_count')
            )
            .join(Order, Order.user_id == User.id)
            .join(Photo, Photo.order_id == Order.id)
            .group_by(User.id, User.fio)
            .order_by(func.count(Photo.id).desc())
            .limit(5)
            .all()
        )

        # Completion rate
        completion_rate = round(
            (orders_with_photos / orders_count * 100) if orders_count > 0 else 0, 1
        )

        return {
            'users_count': users_count,
            'orders_count': orders_count,
            'photos_count': photos_count,
            'orders_with_photos': orders_with_photos,
            'orders_without_photos': orders_without_photos,
            'completion_rate': completion_rate,
            'recent_orders': recent_orders,
            'recent_photos': recent_photos,
            'daily_orders': daily_orders,
            'top_couriers': top_couriers,
        }

    @staticmethod
    def courier_stats(user_id: int) -> dict:
        total_orders = (
            db.session.query(func.count(Order.id))
            .filter(Order.user_id == user_id)
            .scalar() or 0
        )

        # Orders that have at least one photo = confirmed
        confirmed = (
            db.session.query(func.count(distinct(Order.id)))
            .join(Photo, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .scalar() or 0
        )

        pending = total_orders - confirmed

        total_photos = (
            db.session.query(func.count(Photo.id))
            .join(Order, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .scalar() or 0
        )

        # Completion percentage
        completion_pct = round(
            (confirmed / total_orders * 100) if total_orders > 0 else 100, 1
        )

        # Streak: consecutive confirmed orders from most recent
        all_orders = (
            db.session.query(
                Order.id,
                db.session.query(func.count(Photo.id))
                .filter(Photo.order_id == Order.id)
                .correlate(Order)
                .scalar_subquery()
                .label('photo_count')
            )
            .filter(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .all()
        )

        streak = 0
        for row in all_orders:
            if row.photo_count > 0:
                streak += 1
            else:
                break

        # Recent activity
        recent_photos = (
            db.session.query(Photo, Order)
            .join(Order, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .order_by(Photo.uploaded_at.desc())
            .limit(5)
            .all()
        )

        # Pending orders (need photos)
        pending_orders = (
            db.session.query(Order)
            .filter(Order.user_id == user_id)
            .filter(
                ~db.session.query(Photo.id)
                .filter(Photo.order_id == Order.id)
                .exists()
            )
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )

        # Achievements
        achievements = DashboardService._courier_achievements(
            total_orders, confirmed, total_photos, streak
        )

        return {
            'total_orders': total_orders,
            'confirmed': confirmed,
            'pending': pending,
            'total_photos': total_photos,
            'completion_pct': completion_pct,
            'streak': streak,
            'recent_photos': recent_photos,
            'pending_orders': pending_orders,
            'achievements': achievements,
            'all_perfect': pending == 0,
        }

    @staticmethod
    def _courier_achievements(total, confirmed, photos, streak) -> list:
        achievements = []

        def add(name, icon, desc, earned):
            achievements.append({
                'name': name, 'icon': icon,
                'desc': desc, 'earned': earned
            })

        add('Первый заказ', '📦', 'Завершите 1 заказ', confirmed >= 1)
        add('10 заказов', '🔟', 'Завершите 10 заказов', confirmed >= 10)
        add('50 заказов', '📦', 'Завершите 50 заказов', confirmed >= 50)
        add('100 заказов', '💯', 'Завершите 100 заказов', confirmed >= 100)
        add('Серия 5', '🔥', '5 подряд без замечаний', streak >= 5)
        add('Серия 10', '⚡', '10 подряд без замечаний', streak >= 10)
        add('Серия 25', '🌟', '25 подряд без замечаний', streak >= 25)
        add('10 фото', '📸', 'Загрузите 10 фото', photos >= 10)
        add('50 фото', '🖼️', 'Загрузите 50 фото', photos >= 50)
        add('Идеальный день', '✅', 'Все заказы подтверждены', total > 0 and confirmed == total)

        return achievements
