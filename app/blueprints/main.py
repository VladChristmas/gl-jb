import hmac
from datetime import datetime

from flask import Blueprint, jsonify, redirect, request, send_from_directory, session, url_for

from app.extensions import limiter
from app.models.photo import Photo
from config import ADMIN_TOKEN, UPLOAD_FOLDER

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    if session.get("is_admin"):
        return redirect(url_for("admin.dashboard"))
    if session.get("user_id"):
        return redirect(url_for("user.dashboard"))
    return redirect(url_for("auth.unified_login"))


@main_bp.route("/health")
@limiter.exempt
def health_check():
    from sqlalchemy import text

    from app.extensions import db

    payload = {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
    try:
        db.session.execute(text("SELECT 1"))
        payload["database"] = "ok"
    except Exception:
        db.session.rollback()
        payload["status"] = "degraded"
        payload["database"] = "error"
        return jsonify(payload), 503
    return jsonify(payload)


def _is_admin_token() -> bool:
    token = request.headers.get("X-Admin-Token", "")
    return bool(token and ADMIN_TOKEN and hmac.compare_digest(token, ADMIN_TOKEN))


def _uploads_allowed() -> bool:
    """Файлы доступны авторизованной сессии или по X-Admin-Token (скрипты)."""
    if session.get("user_id") or session.get("is_admin"):
        return True
    return _is_admin_token()


def _can_access_upload(filename: str) -> bool:
    """P1-12: файл отдаётся владельцу заказа, админу или по X-Admin-Token.

    Раньше любой залогиненный пользователь мог забрать чужое фото по перебору
    имени файла (IDOR).
    """
    if session.get("is_admin") or _is_admin_token():
        return True
    user_id = session.get("user_id")
    if not user_id:
        return False
    photo = Photo.query.filter_by(filename=filename).first()
    return bool(photo and photo.order and photo.order.user_id == user_id)


@main_bp.route("/uploads/<filename>")
def uploaded_file(filename):
    if not _uploads_allowed():
        return jsonify({"error": "unauthorized"}), 401
    if not _can_access_upload(filename):
        return jsonify({"error": "forbidden"}), 403
    return send_from_directory(UPLOAD_FOLDER, filename)
