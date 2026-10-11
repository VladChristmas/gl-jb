"""Работа с жалобами: добавление в модель N:1 и зеркало в `Order.complaint_*`."""

from datetime import datetime
from typing import Any

from flask_sqlalchemy.pagination import Pagination
from sqlalchemy import and_, func, or_

from app.constants import PHOTO_REQUIRED_TOPICS
from app.extensions import db
from app.models.complaint import Complaint
from app.models.order import Order
from app.models.photo import Photo
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.order_service import date_range_bounds
from app.utils.audit import audit_logger, safe_text


class ComplaintService:
    #: Специальное значение фильтра «жалобы без статуса» (страница /admin/complaints)
    NO_STATUS = "none"

    @staticmethod
    def hydrate_from_mirror(order: Order) -> None:
        """Переносит зеркальные `Order.complaint_*` в строки `Complaint`.

        Нужен для заказов, у которых жалобы существуют только в зеркале
        (легаси-данные или прямая вставка в тестах): без этого новая жалоба
        перезаписала бы старую.
        """
        if order.complaints:
            return
        text = (order.complaint_text or "").strip()
        if not text:
            return
        for part in (chunk.strip() for chunk in text.split("; ")):
            if part:
                order.complaints.append(
                    Complaint(
                        text=part,
                        status=order.complaint_status,
                        created_at=order.complaint_date,
                    )
                )

    @staticmethod
    def sync_mirror(order: Order) -> None:
        """Собирает зеркало `complaint_*` из строк: текст — все жалобы через
        `"; "`, статус и дата — последней жалобы."""
        rows = [c for c in order.complaints if c.text]  # type: ignore[attr-defined]
        if not rows:
            return
        order.complaint_text = "; ".join(c.text for c in rows)
        order.complaint_status = rows[-1].status
        order.complaint_date = rows[-1].created_at

    @staticmethod
    def add(
        order: Order,
        text: str | None,
        status: str | None = None,
        created_at: datetime | None = None,
    ) -> Complaint | None:
        """Добавляет жалобу и обновляет зеркало.

        Повторная строка импорта с тем же текстом не создаёт дубликат —
        раньше такой же guard был на уровне склейки `complaint_text`.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        ComplaintService.hydrate_from_mirror(order)
        reference = safe_text(getattr(order, "order_number", None) or order.id, 60)

        for existing in order.complaints:  # type: ignore[attr-defined]
            if existing.text == cleaned:
                changes = {}
                if status and existing.status != status:
                    changes["status"] = (existing.status, status)
                if created_at and existing.created_at != created_at:
                    changes["created_at"] = (existing.created_at, created_at)
                if status:
                    existing.status = status
                if created_at:
                    existing.created_at = created_at
                ComplaintService.sync_mirror(order)
                # P3-4: обновление существующей жалобы
                audit_logger.info(
                    "complaint update order=%s status=%s text_len=%d",
                    reference,
                    safe_text(status, 40) or "-",
                    len(cleaned),
                )
                if changes:
                    AuditService.log_complaint_update(existing, changes)
                return existing  # type: ignore[no-any-return]

        complaint = Complaint(
            text=cleaned,
            status=status,
            created_at=created_at or datetime.utcnow(),
        )
        order.complaints.append(complaint)
        ComplaintService.sync_mirror(order)
        # P3-4: новая жалоба (текст в лог не пишем — может содержать ПДн)
        audit_logger.info(
            "complaint create order=%s status=%s text_len=%d",
            reference,
            safe_text(status, 40) or "-",
            len(cleaned),
        )
        AuditService.log_complaint_create(order, cleaned, status)
        return complaint

    @staticmethod
    def build_complaints_query(
        search: str = "",
        status: str = "",
        courier: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
    ) -> Any:
        """Общий построитель фильтров для списка жалоб и Excel-выгрузки.

        §12: экспорт после фильтра = выбранная выборка (расхождений нет).
        """
        query: Any = (
            db.session.query(Complaint, Order, User)
            .join(Order, Complaint.order_id == Order.id)
            .join(User, Order.user_id == User.id)
        )
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(or_(Order.order_number.ilike(like), Complaint.text.ilike(like)))
        if status == ComplaintService.NO_STATUS:
            query = query.filter(or_(Complaint.status.is_(None), Complaint.status == ""))
        elif status:
            query = query.filter(Complaint.status == status)
        if courier:
            # Тот же приём, что в OrderService.build_orders_query (SQLite + кириллица)
            query = query.filter(func.trim(Order.courier_fio) == courier.strip())

        start, end = date_range_bounds(date_from, date_to, period)
        if start:
            query = query.filter(Complaint.created_at >= start)
        if end:
            query = query.filter(Complaint.created_at < end)
        return query

    @staticmethod
    def list_complaints(
        page: int = 1,
        per_page: int = 20,
        search: str = "",
        status: str = "",
        courier: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
    ) -> tuple[Pagination, list[dict]]:
        """Страница /admin/complaints: жалобы с заказом и пользователем.

        Только чтение — статусы не меняются (бизнес-правила импорта не трогаем).
        Фильтр `status` — точное совпадение; `NO_STATUS` — жалобы без статуса.
        `date_from`/`date_to`/`period` фильтруют по дате создания жалобы
        (`Complaint.created_at`), а не заказа.
        """
        query = ComplaintService.build_complaints_query(
            search=search,
            status=status,
            courier=courier,
            date_from=date_from,
            date_to=date_to,
            period=period,
        )

        # Один запрос фото вместо N+1 (как в OrderService.get_all_orders)
        photo_counts = dict(
            db.session.query(Photo.order_id, func.count(Photo.id)).group_by(Photo.order_id).all()
        )

        pagination = query.order_by(Complaint.created_at.desc(), Complaint.id.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        rows: list[dict] = []
        for complaint, order, user in pagination.items:
            text = complaint.text or ""
            rows.append(
                {
                    "complaint": complaint,
                    "order": order,
                    "user": user,
                    "photos_count": photo_counts.get(order.id, 0),
                    "photo_required": any(topic in text for topic in PHOTO_REQUIRED_TOPICS),
                }
            )
        return pagination, rows

    @staticmethod
    def export_rows(
        search: str = "",
        status: str = "",
        courier: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
    ) -> list[dict]:
        """Та же выборка, что и список, но без пагинации — для Excel-выгрузки."""
        query = ComplaintService.build_complaints_query(
            search=search,
            status=status,
            courier=courier,
            date_from=date_from,
            date_to=date_to,
            period=period,
        ).order_by(Complaint.created_at.desc(), Complaint.id.desc())

        photo_counts = dict(
            db.session.query(Photo.order_id, func.count(Photo.id)).group_by(Photo.order_id).all()
        )

        rows: list[dict] = []
        for complaint, order, user in query.all():
            text = complaint.text or ""
            rows.append(
                {
                    "complaint": complaint,
                    "order": order,
                    "user": user,
                    "photos_count": photo_counts.get(order.id, 0),
                    "photo_required": any(topic in text for topic in PHOTO_REQUIRED_TOPICS),
                }
            )
        return rows

    @staticmethod
    def courier_summary(
        search: str = "",
        status: str = "",
        date_from: str = "",
        date_to: str = "",
        period: str = "",
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        """Топ курьеров по количеству жалоб (только отображение, рейтинг не трогаем).

        Фильтры те же, что и в списке, кроме самого `courier` — сводка
        сравнивает курьеров между собой. Каждая строка ведёт на список
        жалоб с фильтром courier (§4: показатель → список).
        """
        query = (
            ComplaintService.build_complaints_query(
                search=search,
                status=status,
                date_from=date_from,
                date_to=date_to,
                period=period,
            )
            .with_entities(Order.courier_fio, func.count(Complaint.id))
            .filter(Order.courier_fio.isnot(None), Order.courier_fio != "")
            .group_by(func.trim(Order.courier_fio))
            .order_by(func.count(Complaint.id).desc())
            .limit(limit)
        )
        return [(courier.strip(), count) for courier, count in query.all()]

    @staticmethod
    def status_choices() -> list[tuple[str, str, int]]:
        """(value, label, count) по статусам — значения из данных, не из кода.

        Порядок: известные статусы импорта (открыта → в работе → закрыта),
        затем остальные по алфавиту, в конце «без статуса».
        """
        rows = (
            db.session.query(Complaint.status, func.count(Complaint.id))
            .group_by(Complaint.status)
            .all()
        )
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

        def sort_key(item: tuple[str, int]) -> tuple[int, str]:
            key = item[0]
            if key in known_order:
                return (0, f"{known_order.index(key):02d}")
            return (1, key)

        choices = [
            (key, labels.get(key, key), count) for key, count in sorted(named.items(), key=sort_key)
        ]
        if unnamed_total:
            choices.append((ComplaintService.NO_STATUS, "Без статуса", unnamed_total))
        return choices

    @staticmethod
    def mirror_only_count() -> int:
        """Заказы с зеркальной жалобой, у которых нет строк Complaint.

        Легаси/прямые вставки в БД: гидратация выполняется при импорте и
        миграции. Показываем счётчик честно (§4), не молчим и не пишем в БД.
        """
        return (
            db.session.query(func.count(Order.id))
            .filter(
                and_(
                    Order.complaint_text.isnot(None),
                    Order.complaint_text != "",
                ),
                ~Order.complaints.any(),
            )
            .scalar()
            or 0
        )
