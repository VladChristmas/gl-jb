import hmac
from datetime import datetime

from flask import Blueprint, jsonify, redirect, request, send_from_directory, session, url_for

from app.extensions import limiter
from config import ADMIN_TOKEN, UPLOAD_FOLDER

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
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


def _uploads_allowed() -> bool:
    """Файлы доступны только авторизованной сессии или по X-Admin-Token (скрипты)."""
    if session.get("user_id") or session.get("is_admin"):
        return True
    token = request.headers.get("X-Admin-Token", "")
    if token and ADMIN_TOKEN and hmac.compare_digest(token, ADMIN_TOKEN):
        return True
    return False


@main_bp.route("/uploads/<filename>")
def uploaded_file(filename):
    if not _uploads_allowed():
        return jsonify({"error": "unauthorized"}), 401
    return send_from_directory(UPLOAD_FOLDER, filename)
