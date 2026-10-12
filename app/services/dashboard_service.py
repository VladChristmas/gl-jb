import io
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any

from flask import url_for
from openpyxl import Workbook
from sqlalchemy import and_, case, distinct, func, or_

from app.constants import PHOTO_REQUIRED_TOPICS, ROLE_COURIER
from app.extensions import db
from app.models.complaint import Complaint
from app.models.order import Order
from app.models.photo import Photo
from app.models.user import User
from app.services.date_utils import date_range_bounds


def _photo_required_clause():
    """Заказы, чья жалоба содержит подтему из списка «нужно фото»."""
    return or_(*[Order.complaint_text.ilike(f"%{topic}%") for topic in PHOTO_REQUIRED_TOPICS])


def has_complaint_clause():
    """Заказ считается «с жалобой»: непустое зеркало ИЛИ есть строки `Complaint`.

    Единый признак для всех мест, где считаются жоблы (§5): рейтинг курьеров,
    страница /admin/couriers, фильтр списка заказов.
    """
    return or_(
        and_(Order.complaint_text.isnot(None), Order.complaint_text != ""),
        Order.complaints.any(),
    )


def no_complaint_clause():
    """Заказ без жалоб. NULL-безопасно: `NOT (text != '')` дал бы NULL и прятал
    заказы с незаполненным зеркалом."""
    return and_(
        or_(Order.complaint_text.is_(None), Order.complaint_text == ""),
        ~Order.complaints.any(),
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
        couriers_count = (
            db.session.query(func.count(User.id)).filter(User.role == ROLE_COURIER).scalar() or 0
        )
        orders_count = db.session.query(func.count(Order.id)).scalar() or 0
        photos_count = db.session.query(func.count(Photo.id)).scalar() or 0

        orders_with_photos = db.session.query(func.count(distinct(Photo.order_id))).scalar() or 0

        orders_without_photos = orders_count - orders_with_photos

        # Заказы, которые ждут фото-подтверждения: подтема «нужно фото» и фото ещё нет
        pending_orders = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Photo.id.is_(None), _photo_required_clause())
            .order_by(
                case((Order.address.is_(None) | (Order.address == ""), 1), else_=0),
                Order.created_at.desc(),
                Order.id.desc(),
            )
            .limit(100)
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

        # Уникальные заказы с жалобами (зеркало complaint_text ИЛИ строки Complaint).
        # Отдельный KPI: «жалобы» (штук) и «заказы с жалобами» (штук заказов) —
        # разные величины, на дашборде показываем именно заказы (§4 брифа).
        orders_with_complaints = (
            db.session.query(func.count(Order.id)).filter(has_complaint_clause()).scalar() or 0
        )

        # Best pickers and best couriers (top-5 each)
        best_pickers = DashboardService.picker_stats()[:5]
        best_couriers = DashboardService.best_couriers()[:5]

        # Pending count (orders awaiting photo)
        pending_count = DashboardService.pending_orders_count()

        # Open complaints count (status = "открыта")
        from app.services.complaint_service import ComplaintService

        open_complaints_query = ComplaintService.build_complaints_query(
            status="открыта", date_from="", date_to="", period=""
        )
        open_complaints_count = open_complaints_query.count()
        import_errors_count = 0
        mirror_only_count = ComplaintService.mirror_only_count()

        avg_courier_score = None
        if best_couriers:
            scores = [c["complaint_rate"] for c in best_couriers if c["complaint_rate"] is not None]
            if scores:
                avg_courier_score = round(sum(scores) / len(scores), 1)

        attention_total = pending_count + open_complaints_count + 0 + mirror_only_count

        return {
            "users_count": users_count,
            "couriers_count": couriers_count,
            "orders_count": orders_count,
            "photos_count": photos_count,
            "orders_with_photos": orders_with_photos,
            "orders_without_photos": orders_without_photos,
            "pending_count": DashboardService.pending_orders_count(),
            "orders_with_complaints": orders_with_complaints,
            "open_complaints_count": open_complaints_count,
            "import_errors_count": import_errors_count,
            "mirror_only_count": mirror_only_count,
            "completion_rate": completion_rate,
            "pending_orders": pending_orders,
            "daily_orders": daily_orders,
            "best_couriers": best_couriers,
            "best_pickers": best_pickers,
            "avg_courier_score": avg_courier_score,
            "attention_total": attention_total,
        }

    @staticmethod
    def admin_stats_period(period, date_from="", date_to=""):
        start, end = date_range_bounds(date_from, date_to, period)
        users_count = db.session.query(func.count(User.id)).scalar() or 0
        couriers_count = (
            db.session.query(func.count(User.id)).filter(User.role == ROLE_COURIER).scalar() or 0
        )
        orders_query = db.session.query(Order)
        if start:
            orders_query = orders_query.filter(Order.created_at >= start)
        if end:
            orders_query = orders_query.filter(Order.created_at < end)
        orders_count = orders_query.count()
        photos_count = db.session.query(func.count(Photo.id)).scalar() or 0
        orders_with_photos = db.session.query(func.count(distinct(Photo.order_id))).join(
            Order, Photo.order_id == Order.id
        )
        if start:
            orders_with_photos = orders_with_photos.filter(Order.created_at >= start)
        if end:
            orders_with_photos = orders_with_photos.filter(Order.created_at < end)
        orders_with_photos_count = orders_with_photos.scalar() or 0
        orders_without_photos = orders_count - orders_with_photos_count
        pending_query = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Photo.id.is_(None), _photo_required_clause())
        )
        if start:
            pending_query = pending_query.filter(Order.created_at >= start)
        if end:
            pending_query = pending_query.filter(Order.created_at < end)
        pending_orders = (
            pending_query.order_by(
                case((Order.address.is_(None) | (Order.address == ""), 1), else_=0),
                Order.created_at.desc(),
                Order.id.desc(),
            )
            .limit(100)
            .all()
        )
        # pending_count без фильтра по дате — attention card должен показывать все ожидающие фото
        pending_count = (
            db.session.query(func.count(Order.id))
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Photo.id.is_(None), _photo_required_clause())
            .scalar()
            or 0
        )
        daily_orders_query = db.session.query(
            func.date(Order.created_at).label("day"), func.count(Order.id).label("count")
        )
        if start:
            daily_orders_query = daily_orders_query.filter(Order.created_at >= start)
        if end:
            daily_orders_query = daily_orders_query.filter(Order.created_at < end)
        daily_orders = (
            daily_orders_query.group_by(func.date(Order.created_at))
            .order_by(func.date(Order.created_at))
            .all()
        )
        completion_rate = round(
            (orders_with_photos_count / orders_count * 100) if orders_count > 0 else 0, 1
        )
        orders_with_complaints_query = db.session.query(func.count(Order.id)).filter(
            has_complaint_clause()
        )
        if start:
            orders_with_complaints_query = orders_with_complaints_query.filter(
                Order.created_at >= start
            )
        if end:
            orders_with_complaints_query = orders_with_complaints_query.filter(
                Order.created_at < end
            )
        orders_with_complaints = orders_with_complaints_query.scalar() or 0
        from app.services.complaint_service import ComplaintService

        open_complaints_query = ComplaintService.build_complaints_query(
            status="открыта", date_from=date_from, date_to=date_to, period=period
        )
        open_complaints_count = open_complaints_query.count()
        import_errors_count = 0
        mirror_only_count = ComplaintService.mirror_only_count()
        best_pickers = DashboardService.picker_stats()[:5]
        best_couriers = DashboardService.best_couriers()[:5]
        avg_courier_score = None
        if best_couriers:
            scores = [c["complaint_rate"] for c in best_couriers if c["complaint_rate"] is not None]
            if scores:
                avg_courier_score = round(sum(scores) / len(scores), 1)
        attention_total = (
            pending_count + open_complaints_count + import_errors_count + mirror_only_count
        )
        return {
            "users_count": users_count,
            "couriers_count": couriers_count,
            "orders_count": orders_count,
            "photos_count": photos_count,
            "orders_with_photos": orders_with_photos_count,
            "orders_without_photos": orders_without_photos,
            "pending_count": pending_count,
            "orders_with_complaints": orders_with_complaints,
            "open_complaints_count": open_complaints_count,
            "import_errors_count": import_errors_count,
            "mirror_only_count": mirror_only_count,
            "completion_rate": completion_rate,
            "pending_orders": pending_orders,
            "daily_orders": daily_orders,
            "best_couriers": best_couriers,
            "best_pickers": best_pickers,
            "avg_courier_score": avg_courier_score,
            "attention_total": attention_total,
        }

    @staticmethod
    def pending_orders_count() -> int:
        """Сколько заказов ждут фото-подтверждения (подтема требует фото, фото нет)."""
        return (
            db.session.query(func.count(Order.id))
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Photo.id.is_(None), _photo_required_clause())
            .scalar()
            or 0
        )

    @staticmethod
    def best_couriers(limit: int = 5) -> list[dict]:
        """Рейтинг курьеров (§6: уникальные заказы, заказы с жалобами, количество
        жалоб и процент заказов с жалобами).

        - `orders_count` — уникальные заказы курьера (`count(Order.id)` без join);
        - `orders_with_complaints_count` — заказы, у которых есть жалоба;
        - `complaints_count` — суммарное число жалоб этих заказов (строки
          `Complaint`; у legacy-заказов без строк — части зеркала `complaint_text`);
        - `complaint_rate` = orders_with_complaints_count / orders_count * 100,
          защита от деления на ноль;
        - `delivered_count` / `photo_required_count` — вспомогательные метрики.

        Ранжирование по `complaint_rate` (относительная величина — §6: размер
        выборки у курьеров разный), при равенстве — больше заказов.
        """
        agg_rows = (
            db.session.query(
                Order.courier_fio,
                func.count(Order.id).label("orders_count"),
                func.sum(case((Order.delivered_at.isnot(None), 1), else_=0)).label(
                    "delivered_count"
                ),
                func.sum(case((_photo_required_clause(), 1), else_=0)).label(
                    "photo_required_count"
                ),
            )
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .group_by(Order.courier_fio)
            .all()
        )
        if not agg_rows:
            return []

        # Одна строка на заказ (group_by Order.id), затем агрегация по курьеру в
        # Python: один заказ попадает в счётчик ровно один раз.
        complaint_rows = (
            db.session.query(
                Order.courier_fio,
                Order.complaint_text,
                func.count(Complaint.id).label("complaint_rows"),
            )
            .outerjoin(Complaint, Complaint.order_id == Order.id)
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .group_by(Order.id)
            .all()
        )
        complaint_stats: dict[str, dict] = {}
        for row in complaint_rows:
            key = str(row.courier_fio).strip()
            mirror_parts = [part for part in (row.complaint_text or "").split("; ") if part.strip()]
            rows_count = int(row.complaint_rows or 0)
            if not mirror_parts and rows_count == 0:
                continue
            stats = complaint_stats.setdefault(key, {"orders_with_complaints": 0, "complaints": 0})
            stats["orders_with_complaints"] += 1
            stats["complaints"] += max(rows_count, len(mirror_parts))

        result: list[dict] = []
        for row in agg_rows:
            key = str(row.courier_fio).strip()
            orders_count = int(row.orders_count)
            delivered_count = int(row.delivered_count or 0) or orders_count
            stats = complaint_stats.get(key, {"orders_with_complaints": 0, "complaints": 0})
            orders_with_complaints = int(stats["orders_with_complaints"])
            complaints_count = int(stats["complaints"])
            result.append(
                {
                    "fio": key,
                    "orders_count": orders_count,
                    "delivered_count": delivered_count,
                    "orders_with_complaints_count": orders_with_complaints,
                    "complaints_count": complaints_count,
                    "photo_required_count": int(row.photo_required_count or 0),
                    "complaint_rate": (
                        round(orders_with_complaints / orders_count * 100, 1)
                        if orders_count > 0
                        else 0.0
                    ),
                }
            )
        # §6: не ранжировать по абсолюту — rate относительный; при равенстве больше заказов
        result.sort(key=lambda item: (item["complaint_rate"], -item["orders_count"]))
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

        latest_ids = [row.latest_id for row in agg_rows]
        latest_orders = Order.query.filter(Order.id.in_(latest_ids)).all()
        latest_map = {order.id: order for order in latest_orders}

        # Only load picker users, not all users.
        # Сопоставление в Python: SQLite lower() не работает с кириллицей.
        users_map = {u.fio.strip().lower(): u for u in User.query.all()}

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
        """Complaint orders with courier info: who delivered and must send photos.

        Заказ считается «с жалобой», если непусто зеркало `complaint_text` ИЛИ есть
        строки `Complaint` — тот же признак, что и в `best_couriers` (§5: один
        источник для всех счётчиков жалоб).
        """
        rows = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .filter(has_complaint_clause())
            .order_by(Order.created_at.desc(), Order.id.desc())
            .all()
        )
        # Один запрос вместо запроса фото на каждый заказ (N+1)
        photo_order_ids = {oid for (oid,) in db.session.query(Photo.order_id).distinct()}
        return [
            {
                "order_id": order.id,
                "courier": order.courier_fio or user.fio,
                "address": order.address or "—",
                "delivered_at": order.delivered_at or "—",
                "external_id": order.external_id or "—",
                "order_number": order.order_number or f"#{order.id}",
                "complaint_text": order.complaint_text or "",
                "has_photos": order.id in photo_order_ids,
                # Жалобы ждут фото, если complaint_text содержит одну из подтем списка
                "photo_required": any(
                    topic in (order.complaint_text or "") for topic in PHOTO_REQUIRED_TOPICS
                ),
            }
            for order, user in rows
        ]

    @staticmethod
    def courier_stats(user_id: int) -> dict:
        # Single query for all basic counts
        counts = (
            db.session.query(
                func.count(Order.id).label("total_orders"),
                func.count(distinct(case((Photo.id.isnot(None), Order.id), else_=None))).label(
                    "confirmed"
                ),
                func.count(Photo.id).label("total_photos"),
                func.count(
                    case((and_(Photo.id.is_(None), _photo_required_clause()), Order.id), else_=None)
                ).label("pending"),
            )
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .first()
        )

        if counts is None:
            total_orders = 0
            confirmed = 0
            total_photos = 0
            pending = 0
        else:
            total_orders = int(counts.total_orders or 0)
            confirmed = int(counts.confirmed or 0)
            total_photos = int(counts.total_photos or 0)
            pending = int(counts.pending or 0)

        # Completion percentage
        completion_pct = round((confirmed / total_orders * 100) if total_orders > 0 else 100, 1)

        # Streak: consecutive confirmed orders from most recent
        # Use single query with window function or pre-fetch photo counts
        order_photo_counts = (
            db.session.query(
                Order.id,
                func.count(Photo.id).label("photo_count"),
            )
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Order.user_id == user_id)
            .group_by(Order.id)
            .order_by(Order.created_at.desc())
            .all()
        )

        streak = 0
        for row in order_photo_counts:
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

        # Pending orders (need photos): whitelist photo topics, no photo yet
        pending_orders = (
            db.session.query(Order)
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(
                Order.user_id == user_id,
                Photo.id.is_(None),
                _photo_required_clause(),
            )
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(10)
            .all()
        )

        # Achievements
        achievements = DashboardService._courier_achievements(
            total_orders, confirmed, total_photos, streak, pending
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
    def _match_picker_fio(fio: str) -> str | None:
        """Найти значение picker_fio в заказах, совпадающее с ФИО (без учёта регистра)."""
        target = (fio or "").strip().lower()
        if not target:
            return None
        # Сравнение в Python: SQLite lower() не понимает кириллицу (ASCII-only),
        # поэтому SQL-фильтр lower(...) == target не находит русские ФИО.
        # Distinct-значений мало (число сборщиков), полный обход дёшев.
        rows = (
            db.session.query(Order.picker_fio)
            .filter(Order.picker_fio.isnot(None), Order.picker_fio != "")
            .distinct()
            .all()
        )
        for (value,) in rows:
            if str(value).strip().lower() == target:
                return str(value).strip()
        return None

    @staticmethod
    def picker_dashboard(user: User) -> dict:
        """Дашборд сборщика: его показатели сборки, место в рейтинге и недавние сборки."""
        match = DashboardService._match_picker_fio(user.fio)

        ranked = DashboardService.picker_stats()
        fio_lower = (user.fio or "").strip().lower()
        rank = next(
            (i + 1 for i, item in enumerate(ranked) if item["fio"].strip().lower() == fio_lower),
            None,
        )

        block = DashboardService._picker_block_for_fio(user.fio) if match else None

        recent = []
        if match:
            recent = (
                Order.query.filter(Order.picker_fio == match)
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(20)
                .all()
            )

        achievements = []
        if block:
            picked = int(block.get("pick_count") or 0)
            achievements = [
                {
                    "name": "Первая сборка",
                    "icon": "📦",
                    "desc": "Соберите 1 заказ",
                    "earned": picked >= 1,
                },
                {
                    "name": "25 сборок",
                    "icon": "🧺",
                    "desc": "Соберите 25 заказов",
                    "earned": picked >= 25,
                },
                {
                    "name": "100 сборок",
                    "icon": "💯",
                    "desc": "Соберите 100 заказов",
                    "earned": picked >= 100,
                },
                {
                    "name": "Топ-10",
                    "icon": "🏆",
                    "desc": "Войдите в топ-10 сборщиков",
                    "earned": rank is not None and rank <= 10,
                },
            ]

        return {
            "has_data": block is not None,
            "block": block,
            "recent": recent,
            "rank": rank,
            "pickers_count": len(ranked),
            "achievements": achievements,
        }

    @staticmethod
    def _picker_block_for_fio(fio: str) -> dict | None:
        """Personal picker metrics for a user FIO, or None if not a picker."""
        match = DashboardService._match_picker_fio(fio)
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
    def _courier_achievements(
        total: int, confirmed: int, photos: int, streak: int, pending: int = 0
    ) -> list[dict]:
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
        add("Идеально", "✅", "Нет заказов, ожидающих фото", total > 0 and pending == 0)

        return achievements


class AnalyticsService:
    """Аналитика жалоб: причины, тренды, курьеры, сравнение с объёмом заказов."""

    @staticmethod
    def _extract_complaint_reasons(text: str) -> list[str]:
        """Разбивает текст жалобы на отдельные причины по разделителю '; '."""
        if not text:
            return []
        return [part.strip() for part in text.split("; ") if part.strip()]

    @staticmethod
    def _match_known_topics(reasons: list[str]) -> dict[str, int]:
        """Сопоставляет причины с известными темами (PHOTO_REQUIRED_TOPICS + остальные)."""
        topic_counts: dict[str, int] = {}
        for reason in reasons:
            matched = False
            for topic in PHOTO_REQUIRED_TOPICS:
                if topic.lower() in reason.lower():
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
                    matched = True
                    break
            if not matched:
                # Группируем остальные по первым словам или оставляем как есть
                key = reason[:50] if len(reason) > 50 else reason
                topic_counts[key] = topic_counts.get(key, 0) + 1
        return topic_counts

    @classmethod
    def complaint_reasons(
        cls,
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        courier: str = "",
        status: str = "",
        search: str = "",
    ) -> dict:
        """Агрегация причин жалоб за период с фильтрами.

        Возвращает:
        - top_reasons: список (причина, кол-во) отсортированный по убыванию
        - photo_required_breakdown: разбивка по темам, требующим фото
        - total_complaints: общее число строк Complaint
        - total_orders_with_complaints: уникальных заказов с жалобами
        """
        from app.services.complaint_service import ComplaintService

        start, end = date_range_bounds(date_from, date_to, period)

        # Базовый запрос: Complaint + Order + User
        query = db.session.query(Complaint, Order).join(Order, Complaint.order_id == Order.id)

        # Фильтры
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Order.order_number.ilike(like),
                    Complaint.text.ilike(like),
                    Order.courier_fio.ilike(like),
                )
            )
        if courier:
            query = query.filter(func.trim(Order.courier_fio) == courier.strip())
        if status == ComplaintService.NO_STATUS:
            query = query.filter(or_(Complaint.status.is_(None), Complaint.status == ""))
        elif status:
            query = query.filter(Complaint.status == status)

        if start:
            query = query.filter(Complaint.created_at >= start)
        if end:
            query = query.filter(Complaint.created_at < end)

        rows = query.all()

        # Агрегация причин
        all_reasons: list[str] = []
        orders_with_complaints = set()
        for complaint, order in rows:
            reasons = cls._extract_complaint_reasons(complaint.text or "")
            all_reasons.extend(reasons)
            orders_with_complaints.add(order.id)

        reason_counts = Counter(all_reasons)
        top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)

        # Разбивка по фото-требуемым темам
        photo_breakdown = cls._match_known_topics(list(reason_counts.keys()))
        photo_breakdown_sorted = sorted(photo_breakdown.items(), key=lambda x: x[1], reverse=True)

        return {
            "top_reasons": top_reasons,
            "photo_required_breakdown": photo_breakdown_sorted,
            "total_complaints": len(rows),
            "total_orders_with_complaints": len(orders_with_complaints),
        }

    @classmethod
    def complaint_trends(
        cls,
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        courier: str = "",
        status: str = "",
        search: str = "",
    ) -> dict:
        """Динамика жалоб по дням/неделям с сравнением к заказам."""
        from app.services.complaint_service import ComplaintService

        start, end = date_range_bounds(date_from, date_to, period)

        # Жалобы по дням
        complaints_query = db.session.query(
            func.date(Complaint.created_at).label("day"),
            func.count(Complaint.id).label("count"),
        ).join(Order, Complaint.order_id == Order.id)
        if courier:
            complaints_query = complaints_query.filter(
                func.trim(Order.courier_fio) == courier.strip()
            )
        if status == ComplaintService.NO_STATUS:
            complaints_query = complaints_query.filter(
                or_(Complaint.status.is_(None), Complaint.status == "")
            )
        elif status:
            complaints_query = complaints_query.filter(Complaint.status == status)
        if start:
            complaints_query = complaints_query.filter(Complaint.created_at >= start)
        if end:
            complaints_query = complaints_query.filter(Complaint.created_at < end)
        if search:
            like = f"%{search.strip()}%"
            complaints_query = complaints_query.filter(
                or_(Order.order_number.ilike(like), Complaint.text.ilike(like))
            )

        complaints_by_day = (
            complaints_query.group_by(func.date(Complaint.created_at))
            .order_by(func.date(Complaint.created_at))
            .all()
        )

        # Заказы по тем же дням (для расчёта доли)
        orders_query = db.session.query(
            func.date(Order.created_at).label("day"),
            func.count(Order.id).label("count"),
        )
        if start:
            orders_query = orders_query.filter(Order.created_at >= start)
        if end:
            orders_query = orders_query.filter(Order.created_at < end)
        if courier:
            orders_query = orders_query.filter(func.trim(Order.courier_fio) == courier.strip())

        orders_by_day = (
            orders_query.group_by(func.date(Order.created_at))
            .order_by(func.date(Order.created_at))
            .all()
        )

        # Объединяем в словарь для легкого доступа
        orders_map = {str(day): count for day, count in orders_by_day}
        trends = []
        for day, complaint_count in complaints_by_day:
            day_str = str(day)
            orders_count = orders_map.get(day_str, 0)
            rate = round(complaint_count / orders_count * 100, 1) if orders_count > 0 else 0.0
            trends.append(
                {
                    "day": day_str,
                    "complaints": complaint_count,
                    "orders": orders_count,
                    "rate": rate,
                }
            )

        return {
            "daily_trends": trends,
            "total_complaints": sum(t["complaints"] for t in trends),
            "total_orders": sum(t["orders"] for t in trends),
        }

    @classmethod
    def courier_complaint_stats(
        cls,
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        status: str = "",
        search: str = "",
        limit: int = 20,
    ) -> list[dict]:
        """Статистика жалоб по курьерам: заказы, жалобы, доля, тренд."""
        from app.services.complaint_service import ComplaintService

        start, end = date_range_bounds(date_from, date_to, period)

        # Агрегация по курьерам: заказы + жалобы
        query = (
            db.session.query(
                func.trim(Order.courier_fio),
                func.count(Order.id).label("orders_count"),
                func.count(Complaint.id).label("complaints_count"),
            )
            .outerjoin(Complaint, Complaint.order_id == Order.id)
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
        )

        if status == ComplaintService.NO_STATUS:
            query = query.filter(or_(Complaint.status.is_(None), Complaint.status == ""))
        elif status:
            query = query.filter(Complaint.status == status)
        if start:
            query = query.filter(Order.created_at >= start)
        if end:
            query = query.filter(Order.created_at < end)
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(or_(Order.order_number.ilike(like), Complaint.text.ilike(like)))

        rows = query.group_by(func.trim(Order.courier_fio)).all()

        result = []
        for courier_fio, orders_count, complaints_count in rows:
            rate = round(complaints_count / orders_count * 100, 1) if orders_count > 0 else 0.0
            result.append(
                {
                    "courier": courier_fio.strip(),
                    "orders_count": orders_count,
                    "complaints_count": complaints_count,
                    "complaint_rate": rate,
                }
            )

        # Сортировка: по убыванию доли жалоб, при равенстве — по убыванию заказов
        result.sort(key=lambda x: (-x["complaint_rate"], -x["orders_count"]))
        return result[:limit]

    @classmethod
    def status_breakdown(
        cls,
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        courier: str = "",
        search: str = "",
    ) -> list[dict]:
        """Распределение жалоб по статусам."""
        from app.services.complaint_service import ComplaintService  # noqa: F401

        start, end = date_range_bounds(date_from, date_to, period)

        query = db.session.query(Complaint.status, func.count(Complaint.id)).join(
            Order, Complaint.order_id == Order.id
        )

        if courier:
            query = query.filter(func.trim(Order.courier_fio) == courier.strip())
        if start:
            query = query.filter(Complaint.created_at >= start)
        if end:
            query = query.filter(Complaint.created_at < end)
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(or_(Order.order_number.ilike(like), Complaint.text.ilike(like)))

        rows = query.group_by(Complaint.status).all()

        known_order = ["открыта", "в работе", "закрыта"]
        labels = {"открыта": "Открыта", "в работе": "В работе", "закрыта": "Закрыта"}

        named: dict[str, int] = {}
        unnamed_total = 0
        for status_value, count in rows:
            key = (status_value or "").strip()
            if key:
                named[key] = named.get(key, 0) + count
            else:
                unnamed_total += count

        def sort_key(item):
            key = item[0]
            if key in known_order:
                return (0, f"{known_order.index(key):02d}")
            return (1, key)

        result = [
            {"status": key, "label": labels.get(key, key), "count": count}
            for key, count in sorted(named.items(), key=sort_key)
        ]
        if unnamed_total:
            result.append({"status": "none", "label": "Без статуса", "count": unnamed_total})

        return result

    @classmethod
    def photo_topic_breakdown(
        cls,
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        courier: str = "",
        search: str = "",
    ) -> list[dict]:
        """Разбивка по темам, требующим фото (из PHOTO_REQUIRED_TOPICS)."""
        from app.services.complaint_service import ComplaintService  # noqa: F401

        start, end = date_range_bounds(date_from, date_to, period)

        query = db.session.query(Complaint.text, Complaint.id).join(
            Order, Complaint.order_id == Order.id
        )

        if courier:
            query = query.filter(func.trim(Order.courier_fio) == courier.strip())
        if start:
            query = query.filter(Complaint.created_at >= start)
        if end:
            query = query.filter(Complaint.created_at < end)
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(or_(Order.order_number.ilike(like), Complaint.text.ilike(like)))

        rows = query.all()

        topic_counts = {topic: 0 for topic in PHOTO_REQUIRED_TOPICS}
        other_count = 0

        for text, _ in rows:
            reasons = cls._extract_complaint_reasons(text or "")
            matched_any = False
            for reason in reasons:
                matched = False
                for topic in PHOTO_REQUIRED_TOPICS:
                    if topic.lower() in reason.lower():
                        topic_counts[topic] += 1
                        matched = True
                        matched_any = True
                        break
                if not matched:
                    other_count += 1
            if not matched_any and reasons:
                other_count += len(reasons)

        result: list[dict[str, Any]] = [
            {"topic": topic, "count": count} for topic, count in topic_counts.items() if count > 0
        ]
        result.sort(key=lambda x: -x["count"])

        if other_count:
            result.append({"topic": "Другое", "count": other_count})

        return result

    @classmethod
    def get_filter_options(cls) -> dict:
        """Возвращает доступные значения для фильтров: курьеры, статусы."""
        from app.services.complaint_service import ComplaintService

        # Курьеры с жалобами
        couriers = (
            db.session.query(func.trim(Order.courier_fio), func.count(Complaint.id))
            .outerjoin(Complaint, Complaint.order_id == Order.id)
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .group_by(func.trim(Order.courier_fio))
            .order_by(func.count(Complaint.id).desc())
            .limit(50)
            .all()
        )

        # Статусы
        statuses = ComplaintService.status_choices()

        return {
            "couriers": [(c.strip(), count) for c, count in couriers if c],
            "statuses": [(s, label, cnt) for s, label, cnt in statuses],  # noqa: C416
        }


class WeeklyReportService:
    """Еженедельный отчёт руководителя: заказы, жалобы, качество курьеров, сравнение недель."""

    @staticmethod
    def _fmt_pct(curr, total):
        return round(curr / total * 100, 1) if total > 0 else None

    @staticmethod
    def _week_bounds(monday: date) -> tuple[datetime, datetime]:
        """Полуинтервал недели: [Пн 00:00, Пн+7 00:00)."""
        start = datetime.combine(monday, time.min)
        return start, start + timedelta(days=7)

    @staticmethod
    def _parse_week_param(week: str | None) -> date:
        """Парсит параметр week (YYYY-MM-DD или дд.мм.гггг) в понедельник недели."""
        if not week:
            return date.today() - timedelta(days=date.today().weekday())
        text = week.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                return parsed - timedelta(days=parsed.weekday())
            except ValueError:
                continue
        return date.today() - timedelta(days=date.today().weekday())

    @classmethod
    def week_report(cls, week: str | None = None) -> dict:
        """Полный еженедельный отчёт для выбранной недели (понедельник)."""
        monday = cls._parse_week_param(week)
        start, end = cls._week_bounds(monday)
        prev_monday = monday - timedelta(days=7)
        prev_start, prev_end = cls._week_bounds(prev_monday)
        now = datetime.utcnow()
        is_current = start <= now < end
        is_prev_complete = prev_end <= now

        # --- Заказы ---
        orders_count = (
            db.session.query(func.count(Order.id))
            .filter(Order.created_at >= start, Order.created_at < end)
            .scalar()
            or 0
        )

        orders_prev_count = (
            db.session.query(func.count(Order.id))
            .filter(Order.created_at >= prev_start, Order.created_at < prev_end)
            .scalar()
            or 0
        )

        # Заказы без фото — используем SQL вместо загрузки в память
        orders_without_photos = (
            db.session.query(func.count(Order.id))
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(
                Order.created_at >= start,
                Order.created_at < end,
                Photo.id.is_(None),
            )
            .scalar()
            or 0
        )
        orders_prev_without_photos = (
            db.session.query(func.count(Order.id))
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(
                Order.created_at >= prev_start,
                Order.created_at < prev_end,
                Photo.id.is_(None),
            )
            .scalar()
            or 0
        )

        # --- Жалобы ---
        # Уникальные заказы с жалобами (зеркало ИЛИ строки Complaint)
        orders_with_complaints_query = db.session.query(func.count(Order.id)).filter(
            Order.created_at >= start, Order.created_at < end, has_complaint_clause()
        )
        orders_with_complaints = orders_with_complaints_query.scalar() or 0

        orders_prev_with_complaints_query = db.session.query(func.count(Order.id)).filter(
            Order.created_at >= prev_start, Order.created_at < prev_end, has_complaint_clause()
        )
        orders_prev_with_complaints = orders_prev_with_complaints_query.scalar() or 0

        # Всего строк жалоб (Complaint) за период
        complaints_query = (
            db.session.query(func.count(Complaint.id))
            .join(Order)
            .filter(Complaint.created_at >= start, Complaint.created_at < end)
        )
        complaints_count = complaints_query.scalar() or 0

        complaints_prev_query = (
            db.session.query(func.count(Complaint.id))
            .join(Order)
            .filter(Complaint.created_at >= prev_start, Complaint.created_at < prev_end)
        )
        complaints_prev_count = complaints_prev_query.scalar() or 0

        # Динамика жалоб (предупреждение если текущая неделя неполная)
        complaints_delta = orders_with_complaints - orders_prev_with_complaints
        complaints_rate_delta = None
        if orders_count > 0 and orders_prev_count > 0:
            curr_rate = orders_with_complaints / orders_count * 100
            prev_rate = orders_prev_with_complaints / orders_prev_count * 100
            complaints_rate_delta = round(curr_rate - prev_rate, 1)

        # --- Рейтинг курьеров (используем существующий RatingService) ---
        from app.services.rating_service import RatingService

        rating_report = RatingService.week_report(week=monday.isoformat())

        # --- Проблемные заказы ---
        # Заказы с жалобами, у которых нет фото (подтема требует фото)
        problematic_orders = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(
                Order.created_at >= start,
                Order.created_at < end,
                has_complaint_clause(),
                Photo.id.is_(None),
                _photo_required_clause(),
            )
            .order_by(Order.created_at.desc())
            .limit(20)
            .all()
        )

        # --- Результаты импорта (есть ли история) ---
        from app.services.audit_service import AuditService

        recent_imports = AuditService.get_logs(
            page=1,
            per_page=5,
            action_type="import",
            date_from=start.isoformat(),
            date_to=end.isoformat(),
        )[1]

        # --- Недельная динамика заказов (для спарклайна) ---
        daily_orders = (
            db.session.query(
                func.date(Order.created_at).label("day"),
                func.count(Order.id).label("count"),
            )
            .filter(Order.created_at >= start, Order.created_at < end)
            .group_by(func.date(Order.created_at))
            .order_by(func.date(Order.created_at))
            .all()
        )

        daily_complaints = (
            db.session.query(
                func.date(Complaint.created_at).label("day"),
                func.count(Complaint.id).label("count"),
            )
            .join(Order)
            .filter(Complaint.created_at >= start, Complaint.created_at < end)
            .group_by(func.date(Complaint.created_at))
            .order_by(func.date(Complaint.created_at))
            .all()
        )

        # --- Сводка для карточек ---
        kpi_cards = [
            {
                "label": "Заказов",
                "value": orders_count,
                "delta": orders_count - orders_prev_count,
                "pct": None,
                "link": url_for("admin.orders", period="week"),
            },
            {
                "label": "Без фото",
                "value": orders_without_photos,
                "delta": orders_without_photos - orders_prev_without_photos,
                "pct": cls._fmt_pct(orders_without_photos, orders_count),
                "link": url_for("admin.orders", photo_filter="without_photos", period="week"),
            },
            {
                "label": "Заказов с жалобами",
                "value": orders_with_complaints,
                "delta": complaints_delta,
                "pct": cls._fmt_pct(orders_with_complaints, orders_count),
                "link": url_for("admin.complaints", period="week"),
            },
            {
                "label": "Жалоб (шт.)",
                "value": complaints_count,
                "delta": complaints_count - complaints_prev_count,
                "pct": None,
                "link": url_for("admin.complaints", period="week"),
            },
            {
                "label": "% заказов с жалобами",
                "value": cls._fmt_pct(orders_with_complaints, orders_count),
                "delta": None,
                "pct": None,
                "delta_pct": complaints_rate_delta,
                "link": url_for("admin.couriers", week=monday.isoformat()),
            },
        ]

        # --- Таблица курьеров ---
        courier_rows = []
        for row in rating_report.get("rows", []):
            courier_rows.append(
                {
                    "fio": row["fio"],
                    "place": row["place"],
                    "eligible_orders": row["eligible_orders"],
                    "orders_with_complaints": row["orders_with_complaints"],
                    "complaints_count": row["complaints_count"],
                    "complaint_rate": row["complaint_rate"],
                    "quality_score": row["quality_score"],
                    "direction": row["direction"],
                    "deltas": row["deltas"],
                    "status": row["status"],
                }
            )
        for row in rating_report.get("insufficient", []):
            courier_rows.append(
                {
                    "fio": row["fio"],
                    "place": None,
                    "eligible_orders": row["eligible_orders"],
                    "orders_with_complaints": row["orders_with_complaints"],
                    "complaints_count": row["complaints_count"],
                    "complaint_rate": row["complaint_rate"],
                    "quality_score": None,
                    "direction": row["direction"],
                    "deltas": row["deltas"],
                    "status": "insufficient",
                }
            )

        return {
            "monday": monday,
            "start": start,
            "end": end,
            "label": f"{monday.strftime('%d.%m')} — {(monday + timedelta(days=6)).strftime('%d.%m.%Y')}",
            "is_current": is_current,
            "is_prev_complete": is_prev_complete,
            "kpi_cards": kpi_cards,
            "orders_count": orders_count,
            "orders_prev_count": orders_prev_count,
            "orders_without_photos": orders_without_photos,
            "orders_prev_without_photos": orders_prev_without_photos,
            "orders_with_complaints": orders_with_complaints,
            "orders_prev_with_complaints": orders_prev_with_complaints,
            "complaints_count": complaints_count,
            "complaints_prev_count": complaints_prev_count,
            "complaints_delta": complaints_delta,
            "complaints_rate_delta": complaints_rate_delta,
            "rating": rating_report,
            "courier_rows": courier_rows,
            "problematic_orders": problematic_orders,
            "recent_imports": recent_imports,
            "daily_orders": daily_orders,
            "daily_complaints": daily_complaints,
            "week_links": {
                "prev": prev_monday.isoformat(),
                "next": (monday + timedelta(days=7)).isoformat(),
                "now": monday.isoformat(),
            },
        }

    @classmethod
    def export_excel(cls, week: str | None = None) -> bytes:
        """Экспорт еженедельного отчёта в Excel."""
        report = cls.week_report(week)
        wb = Workbook()

        # Лист 1: Сводка
        ws1 = wb.active
        ws1.title = "Сводка"
        headers = ["Показатель", "Текущая неделя", "Предыдущая неделя", "Изменение"]
        ws1.append(headers)

        def add_row(label, curr, prev, delta=None, pct_curr=None, pct_prev=None):
            curr_str = str(curr) if curr is not None else "—"
            prev_str = str(prev) if prev is not None else "—"
            delta_str = ""
            if delta is not None:
                sign = "+" if delta > 0 else ""
                delta_str = f"{sign}{delta}"
            if pct_curr is not None and pct_prev is not None:
                delta_str += (
                    f" ({'+' if pct_curr - pct_prev > 0 else ''}{round(pct_curr - pct_prev, 1)}%)"
                )
            ws1.append([label, curr_str, prev_str, delta_str])

        add_row(
            "Заказов",
            report["orders_count"],
            report["orders_prev_count"],
            report["orders_count"] - report["orders_prev_count"],
        )
        add_row(
            "Без фото",
            report["orders_without_photos"],
            report["orders_prev_without_photos"],
            report["orders_without_photos"] - report["orders_prev_without_photos"],
            cls._fmt_pct(report["orders_without_photos"], report["orders_count"]),
            cls._fmt_pct(report["orders_prev_without_photos"], report["orders_prev_count"]),
        )
        add_row(
            "Заказов с жалобами",
            report["orders_with_complaints"],
            report["orders_prev_with_complaints"],
            report["complaints_delta"],
            cls._fmt_pct(report["orders_with_complaints"], report["orders_count"]),
            cls._fmt_pct(report["orders_prev_with_complaints"], report["orders_prev_count"]),
        )
        add_row(
            "Жалоб (шт.)",
            report["complaints_count"],
            report["complaints_prev_count"],
            report["complaints_count"] - report["complaints_prev_count"],
        )
        add_row(
            "% заказов с жалобами",
            cls._fmt_pct(report["orders_with_complaints"], report["orders_count"]),
            cls._fmt_pct(report["orders_prev_with_complaints"], report["orders_prev_count"]),
            None,
            cls._fmt_pct(report["orders_with_complaints"], report["orders_count"]),
            cls._fmt_pct(report["orders_prev_with_complaints"], report["orders_prev_count"]),
        )

        # Лист 2: Курьеры
        ws2 = wb.create_sheet("Курьеры")
        courier_headers = [
            "Место",
            "Курьер",
            "Заказов в периоде",
            "Заказов с жалобами",
            "Жалоб (шт.)",
            "% с жалобами",
            "Балл",
            "Динамика",
        ]
        ws2.append(courier_headers)
        for row in report["courier_rows"]:
            place = str(row["place"]) if row["place"] else "—"
            score = f"{row['quality_score']:.1f}" if row["quality_score"] is not None else "—"
            rate = f"{row['complaint_rate']:.1f}" if row["complaint_rate"] is not None else "—"
            direction = row["direction"] or "—"
            if row["deltas"]:
                direction += (
                    f" (балл {row['deltas']['score']:+.1f}, заказы {row['deltas']['orders']:+d})"
                )
            ws2.append(
                [
                    place,
                    row["fio"],
                    row["eligible_orders"],
                    row["orders_with_complaints"],
                    row["complaints_count"],
                    rate,
                    score,
                    direction,
                ]
            )

        # Лист 3: Проблемные заказы
        ws3 = wb.create_sheet("Проблемные заказы")
        problem_headers = [
            "№ п/п",
            "ID заказа",
            "Номер заказа",
            "Курьер",
            "Адрес",
            "Время доставки",
            "Текст жалобы",
            "Требуется фото",
        ]
        ws3.append(problem_headers)
        for idx, (order, user) in enumerate(report["problematic_orders"], 1):
            ws3.append(
                [
                    idx,
                    order.id,
                    order.order_number or f"#{order.id}",
                    order.courier_fio or user.fio,
                    order.address or "—",
                    order.delivered_at or "—",
                    order.complaint_text or "",
                    "Да",
                ]
            )

        # Лист 4: Дневная динамика
        ws4 = wb.create_sheet("Дневная динамика")
        daily_headers = ["Дата", "Заказов", "Жалоб", "% жалоб"]
        ws4.append(daily_headers)
        orders_map = {str(day): count for day, count in report["daily_orders"]}
        complaints_map = {str(day): count for day, count in report["daily_complaints"]}
        all_days = sorted(set(orders_map.keys()) | set(complaints_map.keys()))
        for day in all_days:
            o = orders_map.get(day, 0)
            c = complaints_map.get(day, 0)
            rate = round(c / o * 100, 1) if o > 0 else 0.0
            ws4.append([day, o, c, rate])

        # Лист 5: Импорт
        if report["recent_imports"]:
            ws5 = wb.create_sheet("Импорт")
            import_headers = ["Время", "Действие", "Объект", "Детали"]
            ws5.append(import_headers)
            for log in report["recent_imports"]:
                ws5.append(
                    [
                        log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
                        log.action_type,
                        log.object_type,
                        log.details or "",
                    ]
                )

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.read()
