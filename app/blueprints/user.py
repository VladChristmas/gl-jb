from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.services.dashboard_service import DashboardService
from app.services.email_service import EmailService
from app.services.message_service import MessageService
from app.services.order_service import OrderService
from app.services.photo_service import PhotoService
from app.services.user_service import UserService

user_bp = Blueprint("user", __name__)


def user_required(f):
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.unified_login"))
        return f(*args, **kwargs)

    return decorated_function


@user_bp.route("/dashboard")
@user_required
def dashboard():
    user_id = session["user_id"]
    stats = DashboardService.courier_stats(user_id)
    return render_template("user_dashboard.html", stats=stats)


@user_bp.route("/orders")
@user_required
def orders():
    user_id = session["user_id"]
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "", type=str)

    pagination = OrderService.get_user_orders(user_id, page=page, search=search)
    orders = pagination.items

    for order in orders:
        order.photos_count = len(order.photos)

    return render_template("user_orders.html", orders=orders, pagination=pagination, search=search)


@user_bp.route("/orders/<int:order_id>/upload", methods=["POST"])
@user_required
def upload_photo(order_id):
    user_id = session["user_id"]

    photo = PhotoService.save_photo(request.files.get("photo"), order_id, user_id)

    if not photo:
        flash("Ошибка загрузки фото", "error")
        return redirect(url_for("user.orders"))

    order = OrderService.get_order_by_id(order_id)
    user = UserService.get_user_by_id(user_id)

    if user and order:
        EmailService.send_order_completion_email(order, user)

    flash("Фото загружено", "success")
    return redirect(url_for("user.orders"))


@user_bp.route("/orders/<int:order_id>/photos")
@user_required
def order_photos(order_id):
    user_id = session["user_id"]

    order = OrderService.get_user_order(order_id, user_id)
    if not order:
        flash("Заказ не найден", "error")
        return redirect(url_for("user.orders"))

    photos = PhotoService.get_order_photos(order_id)

    return render_template("user_order_photos.html", order=order, photos=photos)


@user_bp.route("/orders/<int:order_id>/photos/delete/<int:photo_id>", methods=["POST"])
@user_required
def delete_photo(order_id, photo_id):
    user_id = session["user_id"]

    if PhotoService.delete_photo(photo_id, order_id, user_id):
        flash("Фото удалено", "success")
    else:
        flash("Фото не найдено", "error")

    return redirect(url_for("user.order_photos", order_id=order_id))


@user_bp.route("/chat", methods=["GET", "POST"])
def chat():
    """Информационный чат: читают курьеры и сборщики, пишут только администраторы."""
    if not session.get("user_id") and not session.get("is_admin"):
        return redirect(url_for("auth.unified_login"))

    if request.method == "POST":
        if not session.get("is_admin"):
            flash("Писать в чат может только администратор", "error")
            return redirect(url_for("user.chat"))
        if MessageService.create_message(request.form.get("text", "")):
            flash("Сообщение опубликовано", "success")
        else:
            flash("Сообщение не может быть пустым", "error")
        return redirect(url_for("user.chat"))

    return render_template(
        "chat.html",
        messages=MessageService.get_messages(),
        is_admin=bool(session.get("is_admin")),
    )


@user_bp.route("/chat/delete/<int:message_id>", methods=["POST"])
def delete_message(message_id):
    if not session.get("is_admin"):
        flash("Удалять сообщения может только администратор", "error")
        return redirect(url_for("user.chat"))
    if MessageService.delete_message(message_id):
        flash("Сообщение удалено", "success")
    return redirect(url_for("user.chat"))
