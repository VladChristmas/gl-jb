from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import limiter
from app.services.user_service import UserService
from config import ADMIN_PASSWORD, ADMIN_TOKEN

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def unified_login():
    if request.method == "POST":
        token = request.form.get("token", "").strip()

        if not token:
            flash("Введите токен", "error")
            return render_template("unified_login.html")

        if token == ADMIN_TOKEN:
            session["is_admin"] = True
            return redirect(url_for("admin.dashboard"))

        user = UserService.get_user_by_token(token)
        if user:
            session["user_id"] = user.id
            session["user_fio"] = user.fio
            return redirect(url_for("user.dashboard"))
        else:
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
        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin.dashboard"))
        flash("Неверный пароль", "error")
    return render_template("admin_login.html")


@auth_bp.route("/admin/token_login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def admin_token_login():
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if token == ADMIN_TOKEN:
            session["is_admin"] = True
            return redirect(url_for("admin.dashboard"))
        flash("Неверный токен суперадмина", "error")
    return render_template("admin_token_login.html")


@auth_bp.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("auth.admin_login"))
