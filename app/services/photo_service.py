import os
import uuid
from datetime import datetime
from typing import Any, cast

import magic
from flask_sqlalchemy.pagination import Pagination
from flask_sqlalchemy.query import Query as FSQuery
from sqlalchemy import func
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.constants import PHOTO_REQUIRED_TOPICS
from app.extensions import db
from app.models.order import Order
from app.models.photo import Photo
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.dashboard_service import _photo_required_clause
from app.services.order_service import OrderService
from config import ALLOWED_EXTENSIONS, MAX_CONTENT_LENGTH, UPLOAD_FOLDER

# Allowed MIME types
ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
}

# Max file size (16MB by default)
MAX_FILE_SIZE = MAX_CONTENT_LENGTH


class PhotoService:
    @staticmethod
    def allowed_file(filename: str) -> bool:
        """Check if file extension is allowed."""
        return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

    @staticmethod
    def validate_file(file: FileStorage | None) -> tuple[bool, str]:
        """
        Validate uploaded file.
        Returns (is_valid, error_message).
        """
        if not file or not file.filename:
            return False, "Файл не выбран"

        if not PhotoService.allowed_file(file.filename):
            return False, f'Недопустимый формат. Разрешены: {", ".join(ALLOWED_EXTENSIONS)}'

        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size > MAX_FILE_SIZE:
            return False, f"Файл слишком большой. Максимум: {MAX_FILE_SIZE // (1024*1024)} MB"

        if file_size == 0:
            return False, "Пустой файл"

        # Check MIME type using python-magic
        try:
            file_header = file.read(2048)
            file.seek(0)
            mime_type = magic.from_buffer(file_header, mime=True)
            if mime_type not in ALLOWED_MIME_TYPES:
                return False, f"Недопустимый тип файла: {mime_type}"
        except Exception:
            # If magic fails, fall back to extension check
            pass

        return True, ""

    @staticmethod
    def save_photo(
        file: FileStorage | None,
        order_id: int,
        user_id: int,
        errors: list[str] | None = None,
    ) -> Photo | None:
        """Сохраняет фото; в `errors` кладётся конкретная причина отказа (P2-10):
        «файл не выбран» / «слишком большой» / «недопустимый формат» и т.д."""
        if not file:
            if errors is not None:
                errors.append("файл не выбран")
            return None

        order = OrderService.get_user_order(order_id, user_id)
        if not order:
            if errors is not None:
                errors.append("заказ не найден или вам не принадлежит")
            return None

        # Validate file
        is_valid, error = PhotoService.validate_file(file)
        if not is_valid:
            if errors is not None:
                errors.append(error)
            return None

        # validate_file уже подтвердил, что расширение допустимо.
        # secure_filename('фото.jpg') даёт 'jpg' (без точки) — берём расширение
        # из исходного имени, иначе IndexError на кириллических именах.
        raw_name = (file.filename or "").strip()
        original_filename = secure_filename(raw_name)
        if not original_filename or "." not in original_filename:
            original_filename = raw_name
        ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else "jpg"
        if ext not in ALLOWED_EXTENSIONS:
            ext = "jpg"
        # uuid-суффикс: две загрузки в одну секунду не перезаписывают файл
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{order_id}_{timestamp}_{uuid.uuid4().hex[:8]}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        # Ensure upload folder exists
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        file.save(filepath)

        photo = Photo(order_id=order_id, filename=filename, original_filename=original_filename)
        db.session.add(photo)
        db.session.commit()
        OrderService.invalidate_stats_cache()
        AuditService.log_photo_upload(order, photo.id)
        return photo

    @staticmethod
    def get_order_photos(order_id: int) -> list[Photo]:
        photos: list[Photo] = (
            Photo.query.filter_by(order_id=order_id).order_by(Photo.uploaded_at.desc()).all()
        )
        return photos

    @staticmethod
    def counts_by_order(order_ids: list[int]) -> dict[int, int]:
        """Счётчики фото для списка заказов одним запросом (вместо N+1 по order.photos)."""
        if not order_ids:
            return {}
        rows = (
            db.session.query(Photo.order_id, db.func.count(Photo.id))
            .filter(Photo.order_id.in_(order_ids))
            .group_by(Photo.order_id)
            .all()
        )
        return {int(order_id): int(count) for order_id, count in rows}

    @staticmethod
    def delete_photo(photo_id: int, order_id: int, user_id: int) -> bool:
        order = OrderService.get_user_order(order_id, user_id)
        if not order:
            return False

        photo = Photo.query.filter_by(id=photo_id, order_id=order_id).first()
        if not photo:
            return False

        return PhotoService._remove_photo(photo, order)

    @staticmethod
    def delete_photo_admin(photo_id: int, order_id: int) -> bool:
        """Удаление фото администратором (без привязки к владельцу заказа)."""
        photo = Photo.query.filter_by(id=photo_id, order_id=order_id).first()
        if not photo:
            return False
        order = OrderService.get_order_by_id(order_id)
        return PhotoService._remove_photo(photo, order)

    @staticmethod
    def _remove_photo(photo: Photo, order: Order | None = None) -> bool:
        """Удаляет файл (если есть) и строку в БД. Файл убирается после коммита."""
        filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
        photo_id = photo.id
        order_id = photo.order_id
        order_number = order.order_number if order else None
        db.session.delete(photo)
        db.session.commit()
        OrderService.invalidate_stats_cache()
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        AuditService.log_photo_delete(photo_id, order_id, order_number)
        return True

    @staticmethod
    def remove_files(filenames: list[str]) -> None:
        """Удаляет файлы из uploads (используется при каскадном удалении заказов/пользователей)."""
        for name in filenames:
            if not name:
                continue
            fp = os.path.join(UPLOAD_FOLDER, name)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass

    # --- Восстановление файлов из локальной резервной копии ПК ---
    # Используется админ-эндпоинтом /admin/api/photos/restore, куда фото
    # заливает scripts/push_photos.ps1 (ПК — источник истины, см. GL_JB_RENDER_REPORT.md).

    @staticmethod
    def restore_name_error(name: object) -> str | None:
        """Проверка имени файла, пришедшего в JSON восстановления.

        Отдельно от `validate_file()`: там валидируется `FileStorage`, здесь —
        строка из JSON, поэтому проверяем путь/длину/расширение сами.
        Возвращает текст ошибки или None, если имя допустимо.
        """
        if not isinstance(name, str) or not name.strip():
            return "пустое имя файла"
        if len(name) > 255:
            return "имя файла слишком длинное"
        if "/" in name or "\\" in name or "\x00" in name:
            return "путь в имени файла запрещён"
        if name in (".", "..") or name.startswith(".."):
            return "недопустимое имя файла"
        if not PhotoService.allowed_file(name):
            return f"недопустимый формат. Разрешены: {', '.join(ALLOWED_EXTENSIONS)}"
        return None

    @staticmethod
    def restore_file(name: str, data: bytes, overwrite: bool = False) -> tuple[str, str]:
        """Записывает файл резервной копии в UPLOAD_FOLDER.

        Возвращает (статус, пояснение):
        `uploaded` — записан;
        `skipped`  — уже есть и размер совпал (идемпотентный повторный прогон);
        `conflict` — файл есть, но размер другой: без `overwrite` не трогаем;
        `failed`   — не записан (пустой, слишком большой, ошибка записи).
        """
        if not data:
            return "failed", "пустой файл"
        if len(data) > MAX_FILE_SIZE:
            return "failed", f"слишком большой (макс. {MAX_FILE_SIZE // (1024 * 1024)} MB)"

        folder = os.path.abspath(UPLOAD_FOLDER)
        dest = os.path.abspath(os.path.join(folder, name))
        # страховка от path traversal, если проверка имени где-то ослабнет
        if not dest.startswith(folder + os.sep):
            return "failed", "недопустимый путь"

        if os.path.isfile(dest):
            if os.path.getsize(dest) == len(data):
                return "skipped", "уже есть, размер совпадает"
            if not overwrite:
                return "conflict", "файл отличается, нужен overwrite"

        try:
            os.makedirs(folder, exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            return "failed", f"ошибка записи: {exc}"
        return "uploaded", ""

    @staticmethod
    def admin_photo_control_stats(
        period: str = "week", date_from: str = "", date_to: str = ""
    ) -> dict:
        from app.services.date_utils import date_range_bounds

        start, end = date_range_bounds(date_from, date_to, period)

        orders_query = db.session.query(Order)
        if start:
            orders_query = orders_query.filter(Order.created_at >= start)
        if end:
            orders_query = orders_query.filter(Order.created_at < end)
        orders_count = orders_query.count()

        orders_with_photos = db.session.query(func.count(func.distinct(Photo.order_id))).join(
            Order, Photo.order_id == Order.id
        )
        if start:
            orders_with_photos = orders_with_photos.filter(Order.created_at >= start)
        if end:
            orders_with_photos = orders_with_photos.filter(Order.created_at < end)
        orders_with_photos_count = orders_with_photos.scalar() or 0

        orders_without_photos = orders_count - orders_with_photos_count

        pending_count_query = (
            db.session.query(func.count(Order.id))
            .outerjoin(Photo, Photo.order_id == Order.id)
            .filter(Photo.id.is_(None), _photo_required_clause())
        )
        if start:
            pending_count_query = pending_count_query.filter(Order.created_at >= start)
        if end:
            pending_count_query = pending_count_query.filter(Order.created_at < end)
        pending_count = pending_count_query.scalar() or 0

        corrupted_query = db.session.query(Photo).join(Order, Photo.order_id == Order.id)
        if start:
            corrupted_query = corrupted_query.filter(Order.created_at >= start)
        if end:
            corrupted_query = corrupted_query.filter(Order.created_at < end)
        corrupted_count = 0
        for photo in corrupted_query.all():
            filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                corrupted_count += 1

        duplicates_count = 0

        return {
            "orders_count": orders_count,
            "orders_with_photos": orders_with_photos_count,
            "orders_without_photos": orders_without_photos,
            "pending_count": pending_count,
            "corrupted_count": corrupted_count,
            "duplicates_count": duplicates_count,
        }

    @staticmethod
    def get_problematic_orders(
        period: str = "week",
        date_from: str = "",
        date_to: str = "",
        problem: str = "all",
        courier: str = "",
        search: str = "",
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[Pagination, list[dict[str, Any]]]:

        from app.services.date_utils import date_range_bounds

        start, end = date_range_bounds(date_from, date_to, period)

        query = (
            db.session.query(Order, User)
            .join(User, Order.user_id == User.id)
            .outerjoin(Photo, Photo.order_id == Order.id)
        )

        if start:
            query = query.filter(Order.created_at >= start)
        if end:
            query = query.filter(Order.created_at < end)

        if courier:
            query = query.filter(func.trim(Order.courier_fio) == courier.strip())

        if search:
            query = query.filter(
                db.or_(
                    Order.order_number.ilike(f"%{search}%"), Order.description.ilike(f"%{search}%")
                )
            )

        if problem == "no_photos":
            query = query.filter(~Order.photos.any())
        elif problem == "required_missing":
            query = query.filter(_photo_required_clause(), ~Order.photos.any())

        query = query.group_by(Order.id, User.id).order_by(Order.created_at.desc(), Order.id.desc())

        # runtime: db.session.query(...) -> flask_sqlalchemy.Query (есть .paginate),
        # статические типы SQLAlchemy видят RowReturningQuery без .paginate
        pagination = cast(FSQuery, query).paginate(page=page, per_page=per_page, error_out=False)

        order_ids = [item[0].id for item in pagination.items]
        photo_counts = PhotoService.counts_by_order(order_ids)

        corrupted_files = set()
        if order_ids:
            photos_in_period = Photo.query.filter(Photo.order_id.in_(order_ids)).all()
            for photo in photos_in_period:
                filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
                if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                    corrupted_files.add(photo.order_id)

        items = []
        for order, user in pagination.items:
            photos_count = photo_counts.get(order.id, 0)
            photo_required = any(
                topic in (order.complaint_text or "") for topic in PHOTO_REQUIRED_TOPICS
            )
            is_corrupted = order.id in corrupted_files

            problem_type = None
            if photos_count == 0 and photo_required:
                problem_type = "required_missing"
            elif photos_count == 0:
                problem_type = "no_photos"
            elif is_corrupted:
                problem_type = "corrupted"
            else:
                problem_type = "ok"

            items.append(
                {
                    "order": order,
                    "user": user,
                    "photos_count": photos_count,
                    "photo_required": photo_required,
                    "problem_type": problem_type,
                    "courier_fio": order.courier_fio or user.fio,
                    "is_corrupted": is_corrupted,
                    "summary": OrderService.order_summary(order),
                }
            )

        if problem != "all":
            items = [item for item in items if item["problem_type"] == problem]
            # Обновляем pagination in-place: total и items
            pagination.total = len(items)
            pagination.items = items

        return pagination, items

    @staticmethod
    def get_user_order(order_id: int, user_id: int) -> Order | None:
        order: Order | None = Order.query.filter_by(id=order_id, user_id=user_id).first()
        return order
