"""Audit service for server-side audit trail.

Provides structured logging to database (AuditLog table) and querying.
"""

from datetime import datetime
from typing import Any

from flask import has_request_context, request, session
from flask_sqlalchemy.pagination import Pagination
from sqlalchemy import desc, func, or_

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.utils.audit import safe_text


class AuditService:
    """Service for writing and reading audit log entries."""

    # Action types
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_IMPORT = "import"
    ACTION_UPLOAD = "upload"
    ACTION_ROLE_CHANGE = "role_change"
    ACTION_TOKEN_REGEN = "token_regen"

    # Object types
    OBJECT_ORDER = "order"
    OBJECT_COMPLAINT = "complaint"
    OBJECT_PHOTO = "photo"
    OBJECT_IMPORT = "import"
    OBJECT_USER = "user"

    @staticmethod
    def _get_actor_info() -> tuple[int | None, str | None]:
        """Get current user_id and fio from session."""
        if not has_request_context():
            return None, None
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user:
                return user_id, user.fio
        return None, None

    @staticmethod
    def _get_request_context() -> tuple[str | None, str | None]:
        """Get IP and request ID from current request."""
        if not has_request_context() or not request:
            return None, None
        ip = request.remote_addr or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or None
        req_id = request.headers.get("X-Request-ID") or None
        return ip, req_id

    @classmethod
    def log(
        cls,
        action_type: str,
        object_type: str,
        object_id: str,
        description: str,
        diff: dict[str, dict[str, Any]] | None = None,
        user_id: int | None = None,
        user_fio: str | None = None,
    ) -> AuditLog:
        """Write an audit log entry to the database.

        Args:
            action_type: create, update, delete, import, upload, role_change, token_regen
            object_type: order, complaint, photo, import, user
            object_id: identifier of the object (order_number, complaint_id, etc.)
            description: human-readable summary (will be sanitized)
            diff: optional dict of field changes: {"field": {"old": ..., "new": ...}}
            user_id: override actor (defaults to current session user)
            user_fio: override actor name (defaults to current session user)

        Returns:
            The created AuditLog entry.
        """
        if user_id is None or user_fio is None:
            session_user_id, session_fio = cls._get_actor_info()
            if user_id is None:
                user_id = session_user_id
            if user_fio is None:
                user_fio = session_fio

        ip, req_id = cls._get_request_context()

        entry = AuditLog(
            user_id=user_id,
            user_fio=safe_text(user_fio, 255) if user_fio else None,
            action_type=action_type,
            object_type=object_type,
            object_id=safe_text(object_id, 100),
            description=safe_text(description, 500),
            diff_json=diff,
            ip_address=ip,
            request_id=req_id,
        )
        db.session.add(entry)
        # Note: caller must commit (usually happens at end of request or in service method)
        return entry

    @classmethod
    def log_order_create(cls, order, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_CREATE,
            object_type=cls.OBJECT_ORDER,
            object_id=order.order_number or str(order.id),
            description=f"Создан заказ: {order.order_number or order.id}",
            diff={"order_number": {"new": order.order_number}, "user_id": {"new": order.user_id}},
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_order_update(cls, order, changes: dict, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        diff = {}
        for field, (old, new) in changes.items():
            if old != new:
                diff[field] = {"old": str(old) if old is not None else None, "new": str(new) if new is not None else None}
        return cls.log(
            action_type=cls.ACTION_UPDATE,
            object_type=cls.OBJECT_ORDER,
            object_id=order.order_number or str(order.id),
            description=f"Изменён заказ: {order.order_number or order.id}" + (f" ({', '.join(diff.keys())})" if diff else ""),
            diff=diff if diff else None,
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_order_delete(cls, order_id: int, order_number: str | None, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_DELETE,
            object_type=cls.OBJECT_ORDER,
            object_id=order_number or str(order_id),
            description=f"Удалён заказ: {order_number or order_id}",
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_complaint_create(cls, order, complaint_text: str, status: str | None, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_CREATE,
            object_type=cls.OBJECT_COMPLAINT,
            object_id=str(order.id),
            description=f"Добавлена жалоба к заказу {order.order_number or order.id}: {safe_text(complaint_text, 80)}",
            diff={"text": {"new": safe_text(complaint_text, 120)}, "status": {"new": status}},
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_complaint_update(cls, complaint, changes: dict, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        diff = {}
        for field, (old, new) in changes.items():
            if old != new:
                diff[field] = {"old": str(old) if old is not None else None, "new": str(new) if new is not None else None}
        return cls.log(
            action_type=cls.ACTION_UPDATE,
            object_type=cls.OBJECT_COMPLAINT,
            object_id=str(complaint.id),
            description=f"Обновлена жалоба #{complaint.id} к заказу {complaint.order.order_number or complaint.order_id}",
            diff=diff if diff else None,
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_photo_upload(cls, order, photo_id: int, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_UPLOAD,
            object_type=cls.OBJECT_PHOTO,
            object_id=str(photo_id),
            description=f"Загружено фото к заказу {order.order_number or order.id}",
            diff={"photo_id": {"new": photo_id}, "order_id": {"new": order.id}},
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_photo_delete(cls, photo_id: int, order_id: int, order_number: str | None, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_DELETE,
            object_type=cls.OBJECT_PHOTO,
            object_id=str(photo_id),
            description=f"Удалено фото #{photo_id} заказа {order_number or order_id}",
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_import(cls, mapping_name: str, mapping_type: str, result: dict, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        """Log import as a single entry with summary counts."""
        return cls.log(
            action_type=cls.ACTION_IMPORT,
            object_type=cls.OBJECT_IMPORT,
            object_id=mapping_name,
            description=f"Импорт '{mapping_name}' ({mapping_type}): создано {result.get('imported', 0)}, обновлено {result.get('updated', 0)}, пропущено {result.get('skipped', 0)}, дублей {result.get('duplicates', 0)}",
            diff={
                "mapping": {"new": mapping_name},
                "type": {"new": mapping_type},
                "imported": {"new": result.get("imported", 0)},
                "updated": {"new": result.get("updated", 0)},
                "skipped": {"new": result.get("skipped", 0)},
                "duplicates": {"new": result.get("duplicates", 0)},
                "errors": {"new": len(result.get("errors", []))},
            },
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_import_failed(cls, mapping_name: str, error: str, user_id: int | None = None, user_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_IMPORT,
            object_type=cls.OBJECT_IMPORT,
            object_id=mapping_name,
            description=f"Импорт '{mapping_name}' завершился с ошибкой: {safe_text(error, 200)}",
            diff={"error": {"new": safe_text(error, 200)}},
            user_id=user_id,
            user_fio=user_fio,
        )

    @classmethod
    def log_user_create(cls, user, actor_id: int | None = None, actor_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_CREATE,
            object_type=cls.OBJECT_USER,
            object_id=str(user.id),
            description=f"Создан пользователь: {user.fio} ({user.role})",
            diff={"fio": {"new": user.fio}, "role": {"new": user.role}},
            user_id=actor_id,
            user_fio=actor_fio,
        )

    @classmethod
    def log_user_role_change(cls, user, old_role: str, new_role: str, actor_id: int | None = None, actor_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_ROLE_CHANGE,
            object_type=cls.OBJECT_USER,
            object_id=str(user.id),
            description=f"Изменена роль пользователя {user.fio}: {old_role} → {new_role}",
            diff={"role": {"old": old_role, "new": new_role}},
            user_id=actor_id,
            user_fio=actor_fio,
        )

    @classmethod
    def log_user_delete(cls, user_fio: str, user_id: int, actor_id: int | None = None, actor_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_DELETE,
            object_type=cls.OBJECT_USER,
            object_id=str(user_id),
            description=f"Удалён пользователь: {user_fio}",
            user_id=actor_id,
            user_fio=actor_fio,
        )

    @classmethod
    def log_user_token_regen(cls, user, actor_id: int | None = None, actor_fio: str | None = None) -> AuditLog:
        return cls.log(
            action_type=cls.ACTION_TOKEN_REGEN,
            object_type=cls.OBJECT_USER,
            object_id=str(user.id),
            description=f"Перегенерирован токен пользователя: {user.fio}",
            user_id=actor_id,
            user_fio=actor_fio,
        )

    @classmethod
    def get_logs(
        cls,
        page: int = 1,
        per_page: int = 50,
        action_type: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        user_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        search: str | None = None,
    ) -> tuple[Pagination, list[dict]]:
        """Query audit logs with filters and pagination."""
        query = db.session.query(AuditLog, User).outerjoin(User, AuditLog.user_id == User.id)

        if action_type:
            query = query.filter(AuditLog.action_type == action_type)
        if object_type:
            query = query.filter(AuditLog.object_type == object_type)
        if object_id:
            query = query.filter(AuditLog.object_id.ilike(f"%{object_id}%"))
        if user_id:
            query = query.filter(AuditLog.user_id == user_id)
        if date_from:
            try:
                dt_from = datetime.strptime(date_from, "%Y-%m-%d")
                query = query.filter(AuditLog.created_at >= dt_from)
            except ValueError:
                pass
        if date_to:
            try:
                dt_to = datetime.strptime(date_to, "%Y-%m-%d")
                # Include entire end day
                from datetime import timedelta
                dt_to = dt_to + timedelta(days=1)
                query = query.filter(AuditLog.created_at < dt_to)
            except ValueError:
                pass
        if search:
            like = f"%{search}%"
            query = query.filter(or_(
                AuditLog.description.ilike(like),
                AuditLog.object_id.ilike(like),
                AuditLog.user_fio.ilike(like),
            ))

        query = query.order_by(desc(AuditLog.created_at), desc(AuditLog.id))
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        rows = []
        for log, user in pagination.items:
            rows.append({
                "log": log,
                "user": user,
            })

        return pagination, rows

    @classmethod
    def get_filter_options(cls) -> dict:
        """Get distinct values for filter dropdowns."""
        action_types = db.session.query(AuditLog.action_type).distinct().all()
        object_types = db.session.query(AuditLog.object_type).distinct().all()
        users = db.session.query(User.id, User.fio).filter(User.fio.isnot(None)).order_by(User.fio).all()

        return {
            "action_types": [a[0] for a in action_types if a[0]],
            "object_types": [o[0] for o in object_types if o[0]],
            "users": [(u[0], u[1]) for u in users],
        }

    @classmethod
    def get_stats(cls) -> dict:
        """Get summary statistics for the audit dashboard."""
        total = db.session.query(func.count(AuditLog.id)).scalar() or 0
        by_action = dict(db.session.query(AuditLog.action_type, func.count(AuditLog.id)).group_by(AuditLog.action_type).all())
        by_object = dict(db.session.query(AuditLog.object_type, func.count(AuditLog.id)).group_by(AuditLog.object_type).all())
        by_user = dict(db.session.query(AuditLog.user_fio, func.count(AuditLog.id)).filter(AuditLog.user_fio.isnot(None)).group_by(AuditLog.user_fio).order_by(func.count(AuditLog.id).desc()).limit(10).all())

        return {
            "total": total,
            "by_action": by_action,
            "by_object": by_object,
            "top_users": by_user,
        }
