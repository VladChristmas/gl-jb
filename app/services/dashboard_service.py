import re
from datetime import datetime, timedelta

from sqlalchemy import case, distinct, func

from app.extensions import db
from app.models.order import Order
from app.models.photo import Photo
from app.models.user import User

# Complaint topics that require photo evidence from the courier
PHOTO_REQUIRED_TOPICS = frozenset(
    {
        "Товар побит/вскрыт",
        "Не донесли часть товаров из заказа",
        "Не учли комментарий к заказу",
        "Принесли чужой заказ",
    }
)


def _to_number(value: object) -> float:
    """Extract first number from a string like '5 мин' / '12,5/час'."""
    if value is None:
        return 0.0
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else 0.0


class DashboardService:
    """Aggregated stats for admin and courier dashboards."""

    @staticmethod
    def admin_stats() -> dict:
        users_count = db.session.query(func.count(User.id)).scalar() or 0
        orders_count = db.session.query(func.count(Order.id)).scalar() or 0
        photos_count = db.session.query(func.count(Photo.id)).scalar() or 0

        orders_with_photos = db.session.query(func.count(distinct(Photo.order_id))).scalar() or 0

        orders_without_photos = orders_count - orders_with_photos

        # Recent orders (last 10)
        recent_orders = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )

        # Orders per day (last 7 days)
        week_ago = datetime.utcnow() - timedelta(days=7)
        daily_orders = (
            db.session.query(
                func.date(Order.created_at).label("day"), func.count(Order.id).label("count")
            )
            .filter(Order.created_at >= week_ago)
            .group_by(func.date(Order.created_at))
            .order_by(func.date(Order.created_at))
            .all()
        )

        # Completion rate
        completion_rate = round(
            (orders_with_photos / orders_count * 100) if orders_count > 0 else 0, 1
        )

        # Best pickers and best couriers (top-5 each)
        best_pickers = DashboardService.picker_stats()[:5]
        best_couriers = DashboardService.best_couriers()[:5]

        return {
            "users_count": users_count,
            "orders_count": orders_count,
            "photos_count": photos_count,
            "orders_with_photos": orders_with_photos,
            "orders_without_photos": orders_without_photos,
            "completion_rate": completion_rate,
            "recent_orders": recent_orders,
            "daily_orders": daily_orders,
            "best_couriers": best_couriers,
            "best_pickers": best_pickers,
        }

    @staticmethod
    def best_couriers(limit: int = 5) -> list[dict]:
        """Лучшие курьеры: меньше обращений (жалоб) на перевезённые заказы — лучше."""
        rows = (
            db.session.query(
                Order.courier_fio,
                func.count(Order.id).label("orders_count"),
                func.sum(
                    case(
                        (
                            Order.complaint_text.isnot(None) & (Order.complaint_text != ""),
                            1,
                        ),
                        else_=0,
                    )
                ).label("complaints_count"),
            )
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .group_by(Order.courier_fio)
            .all()
        )
        result = [
            {
                "fio": str(row.courier_fio).strip(),
                "orders_count": row.orders_count,
                "complaints_count": int(row.complaints_count or 0),
            }
            for row in rows
        ]
        # Наименьшее число жалоб — лучше; при равенстве больше перевезённых заказов
        result.sort(key=lambda item: (item["complaints_count"], -item["orders_count"]))
        return result[:limit]

    @staticmethod
    def picker_stats() -> list[dict]:
        """Aggregated picker stats from imported Excel columns."""
        agg_rows = (
            db.session.query(
                Order.picker_fio,
                func.count(Order.id).label("orders_count"),
                func.max(Order.pick_count).label("max_pick_count"),
                func.max(Order.id).label("latest_id"),
            )
            .filter(Order.picker_fio.isnot(None), Order.picker_fio != "")
            .group_by(Order.picker_fio)
            .all()
        )
        if not agg_rows:
            return []

        latest_orders = Order.query.filter(Order.id.in_([row.latest_id for row in agg_rows])).all()
        latest_map = {order.id: order for order in latest_orders}
        users_map = {user.fio.strip().lower(): user for user in User.query.all()}

        result = []
        for row in agg_rows:
            fio = str(row.picker_fio).strip()
            latest = latest_map.get(row.latest_id)
            wait_time = (latest.wait_time if latest else None) or "—"
            pick_speed = (latest.pick_speed if latest else None) or "—"
            # Рейтинг: чем выше скорость сборки и ниже ожидание — тем лучше
            score = round(_to_number(pick_speed) - _to_number(wait_time), 1)
            result.append(
                {
                    "fio": fio,
                    "pick_count": (
                        row.max_pick_count if row.max_pick_count is not None else row.orders_count
                    ),
                    "wait_time": wait_time,
                    "pick_speed": pick_speed,
                    "score": score,
                    "orders_count": row.orders_count,
                    "user": users_map.get(fio.lower()),
                }
            )
        result.sort(key=lambda item: (item["score"], item["pick_count"]), reverse=True)
        return result

    @staticmethod
    def complaint_couriers() -> list[dict]:
        """Complaint orders with courier info: who delivered and must send photos."""
        rows = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .filter(Order.complaint_text.isnot(None), Order.complaint_text != "")
            .order_by(Order.created_at.desc(), Order.id.desc())
            .all()
        )
        return [
            {
                "order_id": order.id,
                "courier": order.courier_fio or user.fio,
                "address": order.address or "—",
                "delivered_at": order.delivered_at or "—",
                "external_id": order.external_id or "—",
                "order_number": order.order_number or f"#{order.id}",
                "complaint_text": order.complaint_text or "",
                "has_photos": bool(order.photos),
                "photo_required": (order.complaint_text or "").strip() in PHOTO_REQUIRED_TOPICS,
            }
            for order, user in rows
        ]

    @staticmethod
    def courier_stats(user_id: int) -> dict:
        total_orders = (
            db.session.query(func.count(Order.id)).filter(Order.user_id == user_id).scalar() or 0
        )

        # Orders that have at least one photo = confirmed
        confirmed = (
            db.session.query(func.count(distinct(Order.id)))
            .join(Photo, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .scalar()
            or 0
        )

        pending = total_orders - confirmed

        total_photos = (
            db.session.query(func.count(Photo.id))
            .join(Order, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .scalar()
            or 0
        )

        # Completion percentage
        completion_pct = round((confirmed / total_orders * 100) if total_orders > 0 else 100, 1)

        # Streak: consecutive confirmed orders from most recent
        all_orders = (
            db.session.query(
                Order.id,
                db.session.query(func.count(Photo.id))
                .filter(Photo.order_id == Order.id)
                .correlate(Order)
                .scalar_subquery()
                .label("photo_count"),
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
            .filter(~db.session.query(Photo.id).filter(Photo.order_id == Order.id).exists())
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )

        # Achievements
        achievements = DashboardService._courier_achievements(
            total_orders, confirmed, total_photos, streak
        )

        # Picker stats for this user (if their FIO appears in imported picker data)
        user = db.session.get(User, user_id)
        picker_block = None
        if user and user.fio:
            picker_block = DashboardService._picker_block_for_fio(user.fio)

        return {
            "total_orders": total_orders,
            "confirmed": confirmed,
            "pending": pending,
            "total_photos": total_photos,
            "completion_pct": completion_pct,
            "streak": streak,
            "recent_photos": recent_photos,
            "pending_orders": pending_orders,
            "achievements": achievements,
            "all_perfect": pending == 0,
            "picker": picker_block,
        }

    @staticmethod
    def _picker_block_for_fio(fio: str) -> dict | None:
        """Personal picker metrics for a user FIO, or None if not a picker."""
        target = fio.strip().lower()
        distinct_rows = (
            db.session.query(Order.picker_fio).filter(Order.picker_fio.isnot(None)).distinct().all()
        )
        match = next(
            (value for (value,) in distinct_rows if value and value.strip().lower() == target),
            None,
        )
        if match is None:
            return None

        latest = (
            db.session.query(Order)
            .filter(Order.picker_fio == match)
            .order_by(Order.id.desc())
            .first()
        )
        if latest is None:
            return None

        orders_count, max_pick_count = (
            db.session.query(func.count(Order.id), func.max(Order.pick_count))
            .filter(Order.picker_fio == match)
            .one()
        )
        return {
            "pick_count": max_pick_count if max_pick_count is not None else orders_count,
            "wait_time": latest.wait_time or "—",
            "pick_speed": latest.pick_speed or "—",
            "orders_count": orders_count,
        }

    @staticmethod
    def _courier_achievements(total: int, confirmed: int, photos: int, streak: int) -> list[dict]:
        achievements: list[dict] = []

        def add(name: str, icon: str, desc: str, earned: bool) -> None:
            achievements.append({"name": name, "icon": icon, "desc": desc, "earned": earned})

        add("Первый заказ", "📦", "Завершите 1 заказ", confirmed >= 1)
        add("10 заказов", "🔟", "Завершите 10 заказов", confirmed >= 10)
        add("50 заказов", "📦", "Завершите 50 заказов", confirmed >= 50)
        add("100 заказов", "💯", "Завершите 100 заказов", confirmed >= 100)
        add("Серия 5", "🔥", "5 подряд без замечаний", streak >= 5)
        add("Серия 10", "⚡", "10 подряд без замечаний", streak >= 10)
        add("Серия 25", "🌟", "25 подряд без замечаний", streak >= 25)
        add("10 фото", "📸", "Загрузите 10 фото", photos >= 10)
        add("50 фото", "🖼️", "Загрузите 50 фото", photos >= 50)
        add("Идеальный день", "✅", "Все заказы подтверждены", total > 0 and confirmed == total)

        return achievements
