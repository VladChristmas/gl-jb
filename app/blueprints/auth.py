import hmac

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.constants import ROLE_ADMIN
from app.extensions import limiter
from app.services.user_service import UserService
from app.utils.audit import audit_logger
from config import ADMIN_PASSWORD, ADMIN_TOKEN

auth_bp = Blueprint("auth", __name__)


def _secret_equals(value: str, secret: str) -> bool:
    return bool(value) and bool(secret) and hmac.compare_digest(value, secret)


def _client_ip() -> str:
    return request.remote_addr or "-"


def _log_login_ok(method: str, role: str, user_id: int | None = None) -> None:
    """P3-4: успешный вход. Токен/пароль в лог НЕ попадают."""
    audit_logger.info(
        "login success method=%s role=%s user_id=%s ip=%s",
        method,
        role,
        user_id if user_id is not None else "-",
        _client_ip(),
    )


def _log_login_failed(method: str, reason: str, secret: str = "") -> None:
    """P3-4: неудачный вход. Логируется только длина попытки, не значение."""
    audit_logger.warning(
        "login failed method=%s reason=%s attempt_len=%d ip=%s",
        method,
        reason,
        len(secret),
        _client_ip(),
    )


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def unified_login():
    if request.method == "POST":
        token = request.form.get("token", "").strip()

        if not token:
            _log_login_failed("unified", "empty_token")
            flash("Введите токен", "error")
            return render_template("unified_login.html")

        if _secret_equals(token, ADMIN_TOKEN):
            session["is_admin"] = True
            _log_login_ok("unified", ROLE_ADMIN)
            return redirect(url_for("admin.dashboard"))

        user = UserService.get_user_by_token(token)
        if user:
            session["user_id"] = user.id
            session["user_fio"] = user.fio
            session["user_role"] = user.role
            _log_login_ok("unified", user.role, user.id)
            if user.role == ROLE_ADMIN:
                session["is_admin"] = True
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("user.dashboard"))
        else:
            _log_login_failed("unified", "invalid_token", token)
            flash("Неверный токен", "error")

    return render_template("unified_login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.unified_login"))


@auth_bp.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if _secret_equals(password, ADMIN_PASSWORD):
            session["is_admin"] = True
            _log_login_ok("password", ROLE_ADMIN)
            return redirect(url_for("admin.dashboard"))
        _log_login_failed(
            "password", "empty_password" if not password else "invalid_password", password
        )
        flash("Неверный пароль", "error")
    return render_template("admin_login.html")


@auth_bp.route("/admin/token_login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def admin_token_login():
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if _secret_equals(token, ADMIN_TOKEN):
            session["is_admin"] = True
            _log_login_ok("admin_token", ROLE_ADMIN)
            return redirect(url_for("admin.dashboard"))
        _log_login_failed("admin_token", "empty_token" if not token else "invalid_token", token)
        flash("Неверный токен суперадмина", "error")
    return render_template("admin_token_login.html")


@auth_bp.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("auth.admin_login"))
