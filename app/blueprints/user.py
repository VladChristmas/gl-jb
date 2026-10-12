from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.constants import PHOTO_REQUIRED_TOPICS, ROLE_LABELS, ROLE_PICKER
from app.extensions import limiter
from app.services.dashboard_service import DashboardService
from app.services.email_service import EmailService
from app.services.message_service import MessageService
from app.services.order_service import (
    USER_ORDERS_PHOTO_FILTER_VALUES,
    OrderService,
    split_description,
)
from app.services.photo_service import PhotoService
from app.services.user_service import UserService
from app.utils import wants_json

user_bp = Blueprint("user", __name__)


def user_required(f):
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("auth.unified_login"))
        # Роль перечитывается из БД — смена роли действует сразу
        user = UserService.get_user_by_id(user_id)
        if user is None:
            session.clear()
            return redirect(url_for("auth.unified_login"))
        session["user_role"] = user.role
        return f(*args, **kwargs)

    return decorated_function


@user_bp.route("/dashboard")
@user_required
def dashboard():
    user_id = session["user_id"]
    user = UserService.get_user_by_id(user_id)
    if user and user.role == ROLE_PICKER:
        data = DashboardService.picker_dashboard(user)
        return render_template(
            "user_picker_dashboard.html",
            data=data,
            user=user,
            role_labels=ROLE_LABELS,
        )
    stats = DashboardService.courier_stats(user_id)
    # Человекочитаемое описание в «Ожидают фото»: без служебных полей импорта (F-05)
    for order in stats.get("pending_orders", []):
        order.summary = OrderService.order_summary(order)
    return render_template("user_dashboard.html", stats=stats, user=user, role_labels=ROLE_LABELS)


@user_bp.route("/orders")
@user_required
def orders():
    user_id = session["user_id"]
    user = UserService.get_user_by_id(user_id)
    if user and user.role == ROLE_PICKER:
        flash("Сборщики не загружают фото — данные появятся на главной", "info")
        return redirect(url_for("user.dashboard"))
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "", type=str)
    photo_filter = request.args.get("photo", "", type=str)
    if photo_filter not in USER_ORDERS_PHOTO_FILTER_VALUES:
        photo_filter = ""

    pagination = OrderService.get_user_orders(
        user_id, page=page, search=search, photo_filter=photo_filter
    )
    orders = pagination.items

    # Один запрос на все счётчики фото вместо len(order.photos) на каждый заказ (P2-5)
    photo_counts = PhotoService.counts_by_order([order.id for order in orders])
    for order in orders:
        order.photos_count = photo_counts.get(order.id, 0)
        # Человекочитаемое описание: без служебных полей импорта (F-05 UX-аудита)
        order.summary = OrderService.order_summary(order)
        # Подсказка курьеру: по жалобе обязательно фото, а его ещё нет
        order.photo_required = any(
            topic in (order.complaint_text or "") for topic in PHOTO_REQUIRED_TOPICS
        )

    pending_count = OrderService.count_user_orders_pending_photo(user_id)

    return render_template(
        "user_orders.html",
        orders=orders,
        pagination=pagination,
        search=search,
        photo_filter=photo_filter,
        pending_count=pending_count,
    )


@user_bp.route("/orders/<int:order_id>/upload", methods=["POST"])
@limiter.limit("30 per minute")
@user_required
def upload_photo(order_id):
    user_id = session["user_id"]
    # P1-13: fetch() не шлёт X-Requested-With — определяем по нескольким
    # признакам, иначе JSON-клиент получает redirect вместо JSON.
    is_ajax = wants_json(request)

    # В нативной форме два input name="photo" (камера с capture и галерея без
    # него) — браузер отправляет обе части, у неиспользованной filename пустой.
    files = [f for f in request.files.getlist("photo") if f and f.filename]
    save_errors: list[str] = []
    photo = PhotoService.save_photo(
        files[0] if files else None, order_id, user_id, errors=save_errors
    )

    if not photo:
        # P2-10: конкретная причина («слишком большой» / «формат» / «не выбран»)
        detail = save_errors[0] if save_errors else "не удалось сохранить файл"
        message = f"Ошибка загрузки фото: {detail}"
        if is_ajax:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": message,
                    }
                ),
                400,
            )
        flash(message, "error")
        return redirect(url_for("user.orders"))

    order = OrderService.get_order_by_id(order_id)
    user = UserService.get_user_by_id(user_id)

    if user and order:
        # В фоне: синхронный SMTP в POST-запросе держал ответ на загрузку
        # фото десятки секунд. Сбой письма не должен ломать загрузку (§P1-5).
        try:
            EmailService.dispatch_order_completion_email(order, user)
        except Exception:
            current_app.logger.exception("Не удалось поставить письмо в очередь")

    # flash ставится в обеих ветках: после JSON-ответа JS делает reload,
    # и подтверждение «Фото загружено» видно на обновлённой странице
    flash("Фото загружено", "success")
    if is_ajax:
        return jsonify({"ok": True})
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

    # Человеческое описание + служебные поля импорта отдельным блоком (F-05)
    order.summary, order.technical_description = split_description(order.description)

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
