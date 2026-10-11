import time
from datetime import date, datetime, timedelta
from typing import Any, cast

from flask_sqlalchemy.pagination import Pagination
from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models.order import Order
from app.models.photo import Photo
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.dashboard_service import (
    DashboardService,
    _photo_required_clause,
    has_complaint_clause,
    no_complaint_clause,
)
from app.services.date_utils import date_range_bounds

# Simple in-memory cache for dashboard stats
_stats_cache: dict[str, Any] = {"data": None, "ts": 0}
_STATS_TTL = 60  # seconds

# Допустимые значения фильтра статуса фото (пусто = все заказы)
PHOTO_FILTER_ALL = ""
PHOTO_FILTER_RELEVANT = "photo_relevant"
PHOTO_FILTER_VALUES = {"", "all", "with_photos", "without_photos", "photo_relevant"}
PERIOD_VALUES = {"", "day", "week", "month"}
COMPLAINT_FILTER_VALUES = {"", "with_complaints", "without_complaints"}
# Фильтр страницы «Мои заказы» (курьер): фото-подтема без загруженных фото
USER_ORDERS_PENDING = "pending"
USER_ORDERS_PHOTO_FILTER_VALUES = {"", USER_ORDERS_PENDING}


def parse_iso_date(value: str) -> date | None:
    """'YYYY-MM-DD' -> date; всё остальное игнорируется (не ошибка)."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def period_bounds(period: str, today: date | None = None) -> tuple[date | None, date | None]:
    """Границы пресетов (включительно, нижняя и верхняя даты дня)."""
    today = today or datetime.utcnow().date()
    if period == "day":
        return today, today
    if period == "week":
        return today - timedelta(days=6), today
    if period == "month":
        return today - timedelta(days=29), today
    return None, None


def _apply_date_filters(query: Any, start: datetime | None, end: datetime | None) -> Any:
    if start:
        query = query.filter(Order.created_at >= start)
    if end:
        query = query.filter(Order.created_at < end)
    return query


def _apply_photo_filter(query: Any, photo_filter: str) -> Any:
    """Единая логика статуса фото для списка и экспорта.

    Пусто/`all` — все заказы (KPI дашборда = `count(Order.id)`, поэтому список по
    умолчанию должен показывать те же заказы). `photo_relevant` — прежнее
    правило по умолчанию: с фото или ожидающие фото-подтверждения.
    """
    if photo_filter == "with_photos":
        return query.filter(Order.photos.any())
    if photo_filter == "without_photos":
        return query.filter(~Order.photos.any(), _photo_required_clause())
    if photo_filter == PHOTO_FILTER_RELEVANT:
        return query.filter(
            or_(Order.photos.any(), and_(~Order.photos.any(), _photo_required_clause()))
        )
    return query


class OrderService:
    @staticmethod
    def create_order(
        user_id: int, order_number: str, description: str, external_id: str | None = None
    ) -> Order:
        order = Order(
            user_id=user_id,
            order_number=order_number.strip(),
            description=description.strip() if description else "",
            external_id=external_id,
        )
        db.session.add(order)
        db.session.commit()
        OrderService.invalidate_stats_cache()
        AuditService.log_order_create(order)
        return order

    @staticmethod
    def get_order_by_id(order_id: int) -> Order | None:
        return db.session.get(Order, order_id)

    @staticmethod
    def get_order_by_external_id(external_id: str) -> Order | None:
        order: Order | None = Order.query.filter_by(external_id=external_id).first()
        return order

    @staticmethod
    def get_user_orders(
        user_id: int,
        page: int = 1,
        per_page: int = 10,
        search: str = "",
        photo_filter: str = "",
    ) -> Pagination:
        """Заказы пользователя. `photo_filter="pending"` — ждущие фото:
        фото-подтема жалобы есть, фото ещё не загружены (§4)."""
        query = Order.query.filter_by(user_id=user_id)
        if search:
            query = query.filter(
                or_(Order.order_number.ilike(f"%{search}%"), Order.description.ilike(f"%{search}%"))
            )
        if photo_filter == USER_ORDERS_PENDING:
            query = query.filter(_photo_required_clause(), ~Order.photos.any())
        pagination: Pagination = query.order_by(Order.created_at.desc(), Order.id.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        return pagination

    @staticmethod
    def count_user_orders_pending_photo(user_id: int) -> int:
        """Сколько заказов пользователя ждут фото (для фильтра-чипа «Ждут фото»)."""
        return int(
            Order.query.filter(
                Order.user_id == user_id,
                _photo_required_clause(),
                ~Order.photos.any(),
            ).count()
        )

    @staticmethod
    def build_orders_query(
        search: str = "",
        user_filter: str = "",
        photo_filter: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        courier: str = "",
        complaint_filter: str = "",
    ) -> Any:
        """Единственный источник выборки заказов: список и экспорт используют
        один и тот же набор фильтров (§2/§12 — экспорт = выбранная выборка)."""
        if photo_filter not in PHOTO_FILTER_VALUES:
            photo_filter = PHOTO_FILTER_ALL
        if period not in PERIOD_VALUES:
            period = ""
        if complaint_filter not in COMPLAINT_FILTER_VALUES:
            complaint_filter = ""

        query = db.session.query(Order, User).join(User, Order.user_id == User.id)

        if search:
            query = query.filter(
                or_(
                    Order.order_number.ilike(f"%{search}%"),
                    Order.description.ilike(f"%{search}%"),
                    User.fio.ilike(f"%{search}%"),
                )
            )

        if user_filter:
            # Фильтр принимается только числовой ID — иначе 500 (invalid literal for int)
            if user_filter.isdigit():
                query = query.filter(User.id == int(user_filter))
            else:
                user_filter = ""

        if courier:
            # Точное сравнение по значению из выпадающего списка (courier_choices).
            # SQLite lower() не работает с кириллицей, поэтому регистр не нормализуем.
            query = query.filter(func.trim(Order.courier_fio) == courier.strip())

        if complaint_filter == "with_complaints":
            query = query.filter(has_complaint_clause())
        elif complaint_filter == "without_complaints":
            query = query.filter(no_complaint_clause())

        query = _apply_photo_filter(query, photo_filter)
        start, end = date_range_bounds(date_from, date_to, period)
        return _apply_date_filters(query, start, end)

    @staticmethod
    def courier_choices() -> list[str]:
        """Список курьеров для фильтра (уникальные значения из заказов)."""
        rows = (
            db.session.query(Order.courier_fio)
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .distinct()
            .order_by(Order.courier_fio)
            .all()
        )
        return sorted({str(row[0]).strip() for row in rows if row[0]})

    @staticmethod
    def get_all_orders(
        page: int = 1,
        per_page: int = 20,
        search: str = "",
        user_filter: str = "",
        photo_filter: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        courier: str = "",
        complaint_filter: str = "",
    ) -> tuple[Pagination, list[dict]]:
        query = OrderService.build_orders_query(
            search=search,
            user_filter=user_filter,
            photo_filter=photo_filter,
            date_from=date_from,
            date_to=date_to,
            period=period,
            courier=courier,
            complaint_filter=complaint_filter,
        )

        # Pre-fetch photo counts to avoid N+1
        photo_counts = dict(
            db.session.query(Photo.order_id, func.count(Photo.id)).group_by(Photo.order_id).all()
        )

        pagination = query.order_by(Order.created_at.desc(), Order.id.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        orders_with_users = []
        for order, user in pagination.items:
            orders_with_users.append(
                {"order": order, "user": user, "photos_count": photo_counts.get(order.id, 0)}
            )

        return pagination, orders_with_users

    @staticmethod
    def update_order(order_id: int, order_number: str, description: str) -> Order | None:
        order = db.session.get(Order, order_id)
        if order:
            changes = {}
            if order.order_number != order_number.strip():
                changes["order_number"] = (order.order_number, order_number.strip())
            if order.description != (description.strip() if description else ""):
                changes["description"] = (order.description, description.strip() if description else "")
            order.order_number = order_number.strip()
            order.description = description.strip() if description else ""
            db.session.commit()
            if changes:
                AuditService.log_order_update(order, changes)
        return order

    @staticmethod
    def delete_order(order_id: int) -> bool:
        order = db.session.get(Order, order_id)
        if order:
            from app.services.photo_service import PhotoService

            order_number = order.order_number
            filenames = [
                row[0]
                for row in db.session.query(Photo.filename).filter_by(order_id=order_id).all()
            ]
            db.session.delete(order)
            db.session.commit()
            OrderService.invalidate_stats_cache()
            PhotoService.remove_files(filenames)
            AuditService.log_order_delete(order_id, order_number)
            return True
        return False

    @staticmethod
    def get_dashboard_stats() -> dict:
        """KPI дашборда (с кэшем на 60 с).

        §3: раньше здесь был второй, независимый набор COUNT-запросов — источник
        расхождений с `DashboardService.admin_stats`. Теперь обёртка над ним.
        """
        now = time.time()
        if _stats_cache["data"] and (now - _stats_cache["ts"]) < _STATS_TTL:
            return cast(dict, _stats_cache["data"])

        full = DashboardService.admin_stats()
        result = {
            "users_count": full["users_count"],
            "orders_count": full["orders_count"],
            "photos_count": full["photos_count"],
            "orders_with_photos": full["orders_with_photos"],
        }
        _stats_cache["data"] = result
        _stats_cache["ts"] = now
        return result

    @staticmethod
    def invalidate_stats_cache():
        _stats_cache["data"] = None
        _stats_cache["ts"] = 0

    @staticmethod
    def get_order_with_user(order_id: int) -> tuple[Order, User] | None:
        row = db.session.query(Order, User).join(User).filter(Order.id == order_id).first()
        return cast("tuple[Order, User] | None", row)

    @staticmethod
    def get_user_order(order_id: int, user_id: int) -> Order | None:
        order: Order | None = Order.query.filter_by(id=order_id, user_id=user_id).first()
        return order
