"""Еженедельный рейтинг курьеров.

Единственный источник бизнес-логики (§11 брифа): таблица рейтинга на
/admin/couriers, KPI периода, карточка курьера и карточка «Лучшие курьеры»
на дашборде используют `RatingService.week_report()`.

Зафиксированные правила (GL_JB_RATING_REPORT.md §3.2):
- период — календарная неделя Пн..Вс, полуинтервал [Пн 00:00, Пн+7 00:00), UTC;
- заказ относится к неделе по дате из `RATING_DATE_SOURCE` (по умолчанию
  `delivered_at` — парсинг строки из Excel); заказ без распознанной даты в период
  не входит и попадает в счётчик `orders_without_date` (прозрачно, не молча);
- жалобы учитываются по заказу, к которому относятся (неделя заказа);
  отдельный KPI «жалоб зарегистрировано за период» — по дате обращения;
- `complaint_rate` = orders_with_complaints / eligible_orders * 100;
  при eligible_orders = 0 → None (в UI показывается «—», не 0%);
- `quality_score` = clamp(100 - rate * K, 1, 100), K = RATING_PENALTY_PER_PERCENT;
  сырое значение хранится как float, округление только при отображении;
- eligible_orders < RATING_MIN_ORDERS → балла нет, строка в отдельном блоке
  «Недостаточно данных» (там же курьеры-пользователи без заказов);
- равные показатели → одинаковое место (1, 2, 2, 4);
- динамика — сравнение с предыдущей неделей (monday - 7 дней) той же функцией.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import TypeVar, cast

from flask import current_app
from sqlalchemy import func

from app.constants import ROLE_COURIER
from app.extensions import db
from app.models.complaint import Complaint
from app.models.order import Order
from app.models.user import User

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Форматы даты доставки, встречающиеся в Order.delivered_at (строка из Excel)
_DELIVERY_DATE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

WEEK_INPUT_FORMATS = ("%Y-%m-%d", "%d.%m.%Y")

STATUS_OK = "ok"
STATUS_PRELIMINARY = "preliminary"
STATUS_INSUFFICIENT = "insufficient"

STATUS_LABELS = {
    STATUS_OK: "Рассчитан",
    STATUS_PRELIMINARY: "Предварительный рейтинг",
    STATUS_INSUFFICIENT: "Недостаточно данных",
}

DIRECTION_IMPROVED = "Улучшение"
DIRECTION_WORSE = "Ухудшение"
DIRECTION_SAME = "Без изменений"
DIRECTION_UNKNOWN = "Недостаточно данных для сравнения"

SORT_FIELDS = ("score", "rate", "orders", "complaints", "fio")


def parse_delivered_at(value: object) -> datetime | None:
    """Распознать дату из строки `Order.delivered_at`. None, если пусто/иначе."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DELIVERY_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def week_monday(value: object = None, *, today: date | None = None) -> date:
    """Понедельник недели по параметру `week` (YYYY-MM-DD или дд.мм.гггг).

    Любая дата внутри недели нормализуется к её понедельнику. Пустое или
    нераспознанное значение → текущая неделя (UTC, как и весь проект).
    """
    base = today or datetime.utcnow().date()
    if value:
        text = str(value).strip()
        for fmt in WEEK_INPUT_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt).date()
            except ValueError:
                continue
            return parsed - timedelta(days=parsed.weekday())
    return base - timedelta(days=base.weekday())


def week_bounds(monday: date) -> tuple[datetime, datetime]:
    """Полуинтервал недели: [Пн 00:00, Пн+7 00:00)."""
    start = datetime.combine(monday, time.min)
    return start, start + timedelta(days=7)


def week_label(monday: date) -> str:
    end = monday + timedelta(days=6)
    return f"{monday.strftime('%d.%m')} — {end.strftime('%d.%m.%Y')}"


def _config(name: str, default: T) -> T:
    value = current_app.config.get(name, default)
    return cast(T, value)


def _metric_row(fio: str) -> dict:
    return {
        "fio": fio,
        "place": None,
        "eligible_orders": 0,
        "orders_with_complaints": 0,
        "complaints_count": 0,
        "orders_without_date": 0,
        "total_orders": 0,
        "complaint_rate": None,
        "quality_score": None,
        "status": STATUS_INSUFFICIENT,
        "status_label": STATUS_LABELS[STATUS_INSUFFICIENT],
        "prev": None,
        "direction": DIRECTION_UNKNOWN,
        "deltas": None,
    }


class RatingService:
    """Расчёт еженедельного рейтинга курьеров."""

    @staticmethod
    def week_report(
        week: object = None,
        *,
        sort: str = "score",
        sort_dir: str = "desc",
        search: str = "",
        detail_fio: str | None = None,
    ) -> dict:
        """Полный отчёт за неделю.

        Возвращает период, KPI, строки с баллом и местами, отдельный блок
        «Недостаточно данных», динамику к прошлой неделе и (опционально)
        заказы курьера за неделю для карточки.
        """
        monday = week_monday(week)
        start, end = week_bounds(monday)
        prev_monday = monday - timedelta(days=7)
        is_current = start <= datetime.utcnow() < end

        min_orders = int(_config("RATING_MIN_ORDERS", 10))
        penalty_k = float(_config("RATING_PENALTY_PER_PERCENT", 1.0))
        date_source = str(_config("RATING_DATE_SOURCE", "delivered_at"))

        records = RatingService._load_courier_orders(date_source)

        metrics = RatingService._aggregate(records, monday)
        # Курьеры-пользователи без единого заказа не должны исчезать молча (§6)
        for user in User.query.filter(User.role == ROLE_COURIER, User.fio.isnot(None)).all():
            fio = str(user.fio or "").strip()
            if fio:
                metrics.setdefault(fio, _metric_row(fio))

        prev_metrics = RatingService._aggregate(records, prev_monday)

        rows = RatingService._finalize(
            metrics, min_orders=min_orders, penalty_k=penalty_k, is_current=is_current
        )
        prev_rows = RatingService._finalize(
            prev_metrics, min_orders=min_orders, penalty_k=penalty_k, is_current=False
        )
        prev_ranked = [row for row in prev_rows if row["quality_score"] is not None]
        RatingService._assign_places(prev_ranked)
        prev_by_fio = {row["fio"]: row for row in prev_ranked}

        ranked = [row for row in rows if row["quality_score"] is not None]
        insufficient = [row for row in rows if row["quality_score"] is None]
        RatingService._assign_places(ranked)

        for row in ranked:
            RatingService._attach_dynamics(row, prev_by_fio.get(row["fio"]))

        search_text = (search or "").strip().lower()
        if search_text:
            ranked = [row for row in ranked if search_text in row["fio"].lower()]
            insufficient = [row for row in insufficient if search_text in row["fio"].lower()]

        ranked = RatingService._sort_rows(ranked, sort, sort_dir)
        insufficient.sort(key=lambda row: (-row["eligible_orders"], row["fio"]))

        complaints_registered = (
            db.session.query(func.count(Complaint.id))
            .filter(Complaint.created_at >= start, Complaint.created_at < end)
            .scalar()
            or 0
        )

        all_rows = ranked + insufficient
        orders_total = sum(row["eligible_orders"] for row in all_rows)
        orders_with_complaints = sum(row["orders_with_complaints"] for row in all_rows)
        report = {
            "monday": monday,
            "start": start,
            "end": end,
            "label": week_label(monday),
            "is_current": is_current,
            "rows": ranked,
            "insufficient": insufficient,
            "kpi": {
                "couriers_count": len(all_rows),
                "orders_total": orders_total,
                "orders_without_date": sum(row["orders_without_date"] for row in all_rows),
                "orders_with_complaints": orders_with_complaints,
                "complaints_count": sum(row["complaints_count"] for row in all_rows),
                "complaints_registered": int(complaints_registered),
                # None → в UI «—», а не 0%
                "complaint_rate": (
                    round(orders_with_complaints / orders_total * 100, 1) if orders_total else None
                ),
            },
            "min_orders": min_orders,
            "penalty_k": penalty_k,
            "date_source": date_source,
        }

        if detail_fio:
            report["detail_orders"] = [
                record
                for record in records
                if record["courier"] == detail_fio
                and record["dt"] is not None
                and start <= record["dt"] < end
            ]
            report["detail_row"] = next((row for row in rows if row["fio"] == detail_fio), None)
        # Week links for navigation
        prev_monday = monday - timedelta(days=7)
        next_monday = monday + timedelta(days=7)
        report["week_links"] = {
            "prev": prev_monday.isoformat(),
            "next": next_monday.isoformat(),
            "now": monday.isoformat(),
        }

        return report

    @staticmethod
    def _load_courier_orders(date_source: str) -> list[dict]:
        """Один запрос: все заказы с курьером + число строк `Complaint`.

        Потом агрегация в Python — один заказ попадает в счётчик ровно один раз
        (строки `Complaint` заранее схлопнуты `group_by(Order.id)`).
        """
        rows = (
            db.session.query(
                Order.id,
                Order.order_number,
                Order.external_id,
                Order.courier_fio,
                Order.delivered_at,
                Order.created_at,
                Order.complaint_text,
                func.count(Complaint.id).label("complaint_rows"),
            )
            .outerjoin(Complaint, Complaint.order_id == Order.id)
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .group_by(Order.id)
            .all()
        )
        records = []
        for row in rows:
            if date_source == "created_at":
                # Без смешения источников внутри одного расчёта
                dt: datetime | None = row.created_at
            else:
                dt = parse_delivered_at(row.delivered_at)
            mirror_parts = [part for part in (row.complaint_text or "").split("; ") if part.strip()]
            complaint_rows = int(row.complaint_rows or 0)
            # Ограничение модели: у legacy-заказов без строк Complaint число жалоб
            # восстанавливается разбиением зеркала (нет ID жалобы в старых данных)
            complaints = max(complaint_rows, len(mirror_parts))
            records.append(
                {
                    "order_id": row.id,
                    "order_number": row.order_number,
                    "external_id": row.external_id,
                    "courier": str(row.courier_fio).strip(),
                    "delivered_at": row.delivered_at,
                    "dt": dt,
                    "complaints": complaints,
                    "has_complaint": complaints > 0,
                    "complaint_text": row.complaint_text or "",
                }
            )
        return records

    @staticmethod
    def _aggregate(records: list[dict], monday: date) -> dict[str, dict]:
        start, end = week_bounds(monday)
        metrics: dict[str, dict] = {}
        for record in records:
            fio = record["courier"]
            if not fio:
                continue
            row = metrics.setdefault(fio, _metric_row(fio))
            row["total_orders"] += 1
            dt = record["dt"]
            if dt is None:
                row["orders_without_date"] += 1
                continue
            if not (start <= dt < end):
                continue
            row["eligible_orders"] += 1
            if record["has_complaint"]:
                row["orders_with_complaints"] += 1
                row["complaints_count"] += record["complaints"]
        return metrics

    @staticmethod
    def _finalize(
        metrics: dict[str, dict], *, min_orders: int, penalty_k: float, is_current: bool
    ) -> list[dict]:
        for row in metrics.values():
            eligible = row["eligible_orders"]
            if eligible > 0:
                row["complaint_rate"] = row["orders_with_complaints"] / eligible * 100
            if eligible == 0 or eligible < min_orders:
                row["status"] = STATUS_INSUFFICIENT
                row["quality_score"] = None
            else:
                rate = row["complaint_rate"] or 0.0
                raw = 100.0 - rate * penalty_k
                row["quality_score"] = max(1.0, min(100.0, raw))
                row["status"] = STATUS_PRELIMINARY if is_current else STATUS_OK
            row["status_label"] = STATUS_LABELS[row["status"]]
        return list(metrics.values())

    @staticmethod
    def _assign_places(rows: list[dict]) -> None:
        """Места: балл ↓, rate ↑, заказов ↑; равные показатели → одно место."""
        rows.sort(
            key=lambda row: (
                -(row["quality_score"] or 0.0),
                row["complaint_rate"] or 0.0,
                -row["eligible_orders"],
                row["fio"],
            )
        )
        place = 0
        prev_key: tuple | None = None
        for index, row in enumerate(rows, start=1):
            key = (row["quality_score"], row["complaint_rate"], row["eligible_orders"])
            if key != prev_key:
                place = index
                prev_key = key
            row["place"] = place

    @staticmethod
    def _attach_dynamics(row: dict, prev: dict | None) -> None:
        if prev is None:
            row["direction"] = DIRECTION_UNKNOWN
            return
        row["prev"] = {
            "place": prev["place"],
            "quality_score": prev["quality_score"],
            "complaint_rate": prev["complaint_rate"],
            "eligible_orders": prev["eligible_orders"],
            "orders_with_complaints": prev["orders_with_complaints"],
            "complaints_count": prev["complaints_count"],
            "status": prev["status"],
        }
        score = row["quality_score"]
        prev_score = prev["quality_score"]
        if score is None or prev_score is None:
            row["direction"] = DIRECTION_UNKNOWN
            return
        delta = score - prev_score
        if delta > 0.005:
            row["direction"] = DIRECTION_IMPROVED
        elif delta < -0.005:
            row["direction"] = DIRECTION_WORSE
        else:
            row["direction"] = DIRECTION_SAME
        prev_rate = prev["complaint_rate"]
        row["deltas"] = {
            "score": delta,
            "orders": row["eligible_orders"] - prev["eligible_orders"],
            "orders_with_complaints": (
                row["orders_with_complaints"] - prev["orders_with_complaints"]
            ),
            "complaints": row["complaints_count"] - prev["complaints_count"],
            "rate": (
                row["complaint_rate"] - prev_rate
                if row["complaint_rate"] is not None and prev_rate is not None
                else None
            ),
            "place": (prev["place"] - row["place"]) if prev["place"] else None,
        }

    @staticmethod
    def _sort_rows(rows: list[dict], sort: str, sort_dir: str) -> list[dict]:
        reverse = sort_dir != "asc"
        key_getters = {
            "score": lambda row: row["quality_score"] or 0.0,
            "rate": lambda row: (
                row["complaint_rate"] if row["complaint_rate"] is not None else -1.0
            ),
            "orders": lambda row: row["eligible_orders"],
            "complaints": lambda row: row["complaints_count"],
            "fio": lambda row: row["fio"],
        }
        getter = key_getters.get(sort, key_getters["score"])
        return sorted(rows, key=lambda row: (getter(row), row["fio"]), reverse=reverse)

    @staticmethod
    def top_couriers(limit: int = 5) -> list[dict]:
        """Топ недели для дашборда — та же бизнес-логика, что и у таблицы."""
        try:
            report = RatingService.week_report()
        except Exception:  # noqa: BLE001 — дашборд не должен падать из-за рейтинга
            logger.exception("Не удалось рассчитать рейтинг для дашборда")
            return []
        rows = sorted(report["rows"], key=lambda row: (row["place"] or 10**6))
        return rows[:limit]
