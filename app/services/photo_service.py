import os
import uuid
from datetime import datetime

import magic
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.order import Order
from app.models.photo import Photo
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
    def save_photo(file: FileStorage | None, order_id: int, user_id: int) -> Photo | None:
        if not file:
            return None

        order = OrderService.get_user_order(order_id, user_id)
        if not order:
            return None

        # Validate file
        is_valid, error = PhotoService.validate_file(file)
        if not is_valid:
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
        return photo

    @staticmethod
    def get_order_photos(order_id: int) -> list[Photo]:
        photos: list[Photo] = (
            Photo.query.filter_by(order_id=order_id).order_by(Photo.uploaded_at.desc()).all()
        )
        return photos

    @staticmethod
    def delete_photo(photo_id: int, order_id: int, user_id: int) -> bool:
        order = OrderService.get_user_order(order_id, user_id)
        if not order:
            return False

        photo = Photo.query.filter_by(id=photo_id, order_id=order_id).first()
        if not photo:
            return False

        return PhotoService._remove_photo(photo)

    @staticmethod
    def delete_photo_admin(photo_id: int, order_id: int) -> bool:
        """Удаление фото администратором (без привязки к владельцу заказа)."""
        photo = Photo.query.filter_by(id=photo_id, order_id=order_id).first()
        if not photo:
            return False
        return PhotoService._remove_photo(photo)

    @staticmethod
    def _remove_photo(photo: Photo) -> bool:
        """Удаляет файл (если есть) и строку в БД. Файл убирается после коммита."""
        filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
        db.session.delete(photo)
        db.session.commit()
        OrderService.invalidate_stats_cache()
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
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

    @staticmethod
    def get_user_order(order_id: int, user_id: int) -> Order | None:
        order: Order | None = Order.query.filter_by(id=order_id, user_id=user_id).first()
        return order
