import base64
import binascii
import hmac
import io
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import openpyxl
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook
from sqlalchemy import func

from app.constants import PHOTO_REQUIRED_TOPICS, ROLE_ADMIN, ROLE_COURIER, ROLE_LABELS, USER_ROLES
from app.extensions import csrf, db, limiter
from app.models import ImportMapping, User
from app.services.audit_service import AuditService
from app.services.complaint_service import ComplaintService
from app.services.dashboard_service import DashboardService
from app.services.email_service import EmailService
from app.services.import_service import ImportService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService, split_description
from app.services.photo_service import PhotoService
from app.services.rating_service import RatingService
from app.services.user_service import UserService
from app.utils.audit import audit_logger, safe_text
from config import ADMIN_TOKEN, UPLOAD_FOLDER

# Period values for filtering
PERIOD_VALUES = {"", "day", "week", "month"}


# Upload size cache (P2-11)
_UPLOAD_SIZE_CACHE = {"ts": 0.0, "value": 0}
_UPLOAD_SIZE_TTL = 60.0


def _upload_size(upload_folder: str) -> int:
    """Размер uploads/ в байтах, кэшированный на 60 секунд, чтобы не дергать os.walk на каждый запрос (P2-11)."""
    now = time.monotonic()
    if now - float(_UPLOAD_SIZE_CACHE["ts"]) < _UPLOAD_SIZE_TTL:
        return int(_UPLOAD_SIZE_CACHE["value"])

    size = 0
    if os.path.exists(upload_folder):
        for root, _dirs, files in os.walk(upload_folder):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.isfile(fp):
                    size += os.path.getsize(fp)
    _UPLOAD_SIZE_CACHE["ts"] = now
    _UPLOAD_SIZE_CACHE["value"] = size
    return size


# Отчёт об ошибках импорта (P2): полный список храним на диске,
# в session-cookie — только указатель. Иначе кука >4093 байт и браузеры
# молча её отбрасывают (отчёт и ссылка на скачивание пропадают).


def _import_report_dir() -> Path:
    path = Path(current_app.instance_path) / "import_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _drop_import_report_file() -> None:
    """Удаляет файл отчёта, на который указывает текущая сессия (если есть)."""
    pointer = session.get("import_report") or {}
    report_id = pointer.get("id") if isinstance(pointer, dict) else None
    if report_id:
        try:
            (_import_report_dir() / f"{report_id}.json").unlink(missing_ok=True)
        except OSError:
            pass


def _purge_old_import_reports(max_age_seconds: int = 24 * 3600) -> None:
    """Убирает осиротевшие отчёты (сессия так и не пришла за скачиванием)."""
    try:
        cutoff = time.time() - max_age_seconds
        for path in _import_report_dir().glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _store_import_report(report: dict[str, Any]) -> None:
    """Полный отчёт — на диск, в сессии — указатель ~150 байт."""
    _purge_old_import_reports()
    _drop_import_report_file()
    report_id = uuid.uuid4().hex
    try:
        (_import_report_dir() / f"{report_id}.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        session["import_report"] = {
            "id": report_id,
            "file": report.get("file", "—"),
            "generated_at": report.get("generated_at", ""),
            "total_errors": len(report.get("errors", [])),
        }
    except OSError:
        # Диск недоступен: оставляем в куке только начало отчёта,
        # чтобы она не превысила лимит и не была отброшена браузером
        session["import_report"] = {
            **report,
            "errors": report.get("errors", [])[:50],
            "total_errors": len(report.get("errors", [])),
        }


def _pop_import_report() -> dict[str, Any] | None:
    """Забирает отчёт из сессии и удаляет его с диска (одноразовое скачивание)."""
    pointer = session.pop("import_report", None)
    if not pointer:
        return None
    report_id = pointer.get("id") if isinstance(pointer, dict) else None
    if not report_id:
        # Аварийный режим: отчёт целиком лежал в сессии
        report = cast(dict[str, Any], pointer)
        report.setdefault("total_errors", len(report.get("errors", [])))
        return report
    try:
        path = _import_report_dir() / f"{report_id}.json"
        report = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        path.unlink(missing_ok=True)
        report.setdefault("total_errors", len(report.get("errors", [])))
        return report
    except (OSError, ValueError, TypeError):
        return {**pointer, "errors": []}


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(f):
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("auth.admin_login"))
        # Роль перепроверяется в БД — отзыв прав действует немедленно
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user is None:
                session.clear()
                return redirect(url_for("auth.admin_login"))
            if user.role != ROLE_ADMIN:
                session.clear()
                flash("Права администратора отозваны", "error")
                return redirect(url_for("auth.unified_login"))
        return f(*args, **kwargs)

    return decorated_function


def _admin_session_valid() -> bool:
    """Админ-сессия для JSON API: роль перепроверяется в БД, как в admin_required.

    Без этого отзыв прав не действовал на /admin/api/* (счётчик, уведомления,
    список файлов, восстановление фото) до истечения сессии.
    """
    if not session.get("is_admin"):
        return False
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if user is None or user.role != ROLE_ADMIN:
            session.clear()
            return False
    return True


def _admin_api_auth_error():
    """Проверка админ-сессии для JSON API.

    Возвращает None при доступе или готовый ответ:
    401 — админ-сессии нет вообще, 403 — сессия есть, но роль отозвана.
    Единый статус для всех /admin/api/* (раньше был 401 или 403 вразнобой).
    """
    if not session.get("is_admin"):
        return jsonify({"error": "unauthorized"}), 401
    if not _admin_session_valid():
        return jsonify({"error": "forbidden"}), 403
    return None


@admin_bp.route("/")
@admin_required
def dashboard():
    stats = DashboardService.admin_stats()

    upload_size = 0
    if os.path.exists(UPLOAD_FOLDER):
        for root, _dirs, files in os.walk(UPLOAD_FOLDER):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.isfile(fp):
                    upload_size += os.path.getsize(fp)

    # P3-UX-6 (аудит 2026-10): внутренний путь (/app/uploads) не показываем в UI
    stats["upload_size"] = upload_size

    # Initial notifications for server-side render (client will poll for updates)
    notifications = NotificationService.get_notifications(limit=10)

    return render_template("admin_dashboard.html", stats=stats, notifications=notifications)


@admin_bp.route("/pickers")
@admin_required
def pickers():
    pickers_data = DashboardService.picker_stats()
    return render_template("admin_pickers.html", pickers=pickers_data)


@admin_bp.route("/couriers")
@admin_required
def couriers():
    """Дашборд курьеров с рейтингом и списком заказов с жалобами."""
    week = request.args.get("week", "", type=str)
    search = request.args.get("search", "", type=str)
    sort = request.args.get("sort", "score", type=str)
    sort_dir = request.args.get("order", "desc", type=str)

    try:
        rating_report = RatingService.week_report(
            week=week,
            sort=sort,
            sort_dir=sort_dir,
            search=search,
        )
        rating_error = None
    except Exception:
        rating_report = None
        rating_error = True

    complaints_data = DashboardService.complaint_couriers()
    # Считаем заказы, ожидающие фото по courier (для совместимости с шаблоном)
    users_count = (
        db.session.query(func.count(User.id)).filter(User.role == ROLE_COURIER).scalar() or 0
    )
    # Считаем ожидающие фото: заказы с подтемами, требующими фото, без фото
    awaiting_photo = sum(
        1 for row in complaints_data if row["photo_required"] and not row["has_photos"]
    )

    return render_template(
        "admin_couriers.html",
        complaints=complaints_data,
        users_count=users_count,
        orders_with_complaints=len(complaints_data),
        awaiting_photo=awaiting_photo,
        rating=rating_report,
        rating_error=rating_error,
        week_param=(
            rating_report.get("monday", "").isoformat()
            if rating_report and rating_report.get("monday")
            else ""
        ),
        week_links=rating_report.get("week_links") if rating_report else None,
        sort=request.args.get("sort", "score"),
        order=request.args.get("order", "desc"),
        search=request.args.get("search", ""),
    )


@admin_bp.route("/courier/<fio>")
@admin_required
def courier_detail(fio):
    """Детальная карточка курьера."""

    week = request.args.get("week", "", type=str)

    rating_report = RatingService.week_report(week=week, search=fio, detail_fio=fio)

    # Find the specific courier in the report
    courier_row = None
    for row in rating_report.get("rows", []) + rating_report.get("insufficient", []):
        if row["fio"] == fio:
            courier_row = row
            break

    if not courier_row:
        # Return 200 with error message to match test expectations
        return (
            render_template(
                "admin_courier_detail.html",
                courier=None,
                rating=rating_report,
                detail_orders=rating_report.get("detail_orders"),
                week_param=(
                    rating_report.get("monday", "").isoformat()
                    if rating_report.get("monday")
                    else ""
                ),
                week_links=rating_report.get("week_links"),
                search=fio,
                error_message="Курьер не найден",
            ),
            200,
        )

    return render_template(
        "admin_courier_detail.html",
        courier=courier_row,
        rating=rating_report,
        detail_orders=rating_report.get("detail_orders"),
        week_param=(
            rating_report.get("monday", "").isoformat() if rating_report.get("monday") else ""
        ),
        week_links=rating_report.get("week_links"),
        search=fio,
    )


@admin_bp.route("/complaints")
@admin_required
def complaints():
    """Список жалоб: строки Complaint, с фильтрами (§4).

    Строится на ComplaintService.list_complaints.
    """
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "", type=str)
    status = request.args.get("status", "", type=str)
    courier = request.args.get("courier", "", type=str)
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    period = request.args.get("period", "", type=str)
    if period not in PERIOD_VALUES:
        period = ""

    choices = ComplaintService.status_choices()
    allowed = {value for value, _label, _count in choices}
    if status and status not in allowed:
        status = ""

    pagination, rows = ComplaintService.list_complaints(
        page=page,
        search=search,
        status=status,
        courier=courier,
        date_from=date_from,
        date_to=date_to,
        period=period,
    )

    mirror_only = ComplaintService.mirror_only_count()

    # Курьеры для селектора: только те, у кого есть жалобы в выборке
    courier_top = ComplaintService.courier_summary(
        search=search,
        status=status,
        date_from=date_from,
        date_to=date_to,
        period=period,
    )

    return render_template(
        "admin_complaints.html",
        pagination=pagination,
        rows=rows,
        search=search,
        status=status,
        courier=courier,
        date_from=date_from,
        date_to=date_to,
        period=period,
        status_choices=choices,
        courier_choices=[c for c, _ in courier_top],
        courier_top=courier_top,
        mirror_only_count=mirror_only,
        total_complaints=pagination.total,
    )


@admin_bp.route("/complaints/export_excel")
@admin_required
def export_complaints_excel():
    """Выгрузка отфильтрованных жалоб в Excel (§12: экспорт после фильтра = выборка)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Жалобы"

    headers = [
        "ID жалобы",
        "Номер заказа",
        "ФИО клиента",
        "Текст жалобы",
        "Статус",
        "Дата создания",
        "Курьер",
        "Кол-во фото",
        "Фото обязательно",
    ]
    ws.append(headers)

    rows = ComplaintService.export_rows(
        search=request.args.get("search", "", type=str),
        status=request.args.get("status", "", type=str),
        courier=request.args.get("courier", "", type=str),
        date_from=request.args.get("date_from", "", type=str),
        date_to=request.args.get("date_to", "", type=str),
        period=request.args.get("period", "", type=str),
    )

    for row in rows:
        complaint = row["complaint"]
        order = row["order"]
        user = row["user"]
        ws.append(
            [
                complaint.id,
                order.order_number,
                user.fio,
                complaint.text,
                complaint.status or "",
                complaint.created_at.strftime("%Y-%m-%d %H:%M:%S") if complaint.created_at else "",
                order.courier_fio or "",
                row["photos_count"],
                "Да" if row["photo_required"] else "Нет",
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f'complaints_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
    )


@admin_bp.route("/analytics")
@admin_required
def analytics():
    """Аналитика жалоб: причины, тренды, курьеры."""
    from app.services.dashboard_service import AnalyticsService

    period = request.args.get("period", "week", type=str)
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    courier = request.args.get("courier", "", type=str)
    status = request.args.get("status", "", type=str)
    search = request.args.get("search", "", type=str)

    # Валидация period
    if period not in PERIOD_VALUES | {"custom", "today", "week", "month"}:
        period = "week"

    reasons_data = AnalyticsService.complaint_reasons(
        date_from=date_from,
        date_to=date_to,
        period=period,
        courier=courier,
        status=status,
        search=search,
    )
    trends_data = AnalyticsService.complaint_trends(
        date_from=date_from,
        date_to=date_to,
        period=period,
        courier=courier,
        status=status,
        search=search,
    )
    courier_stats = AnalyticsService.courier_complaint_stats(
        date_from=date_from, date_to=date_to, period=period, status=status, search=search
    )
    status_breakdown = AnalyticsService.status_breakdown(
        date_from=date_from, date_to=date_to, period=period, courier=courier, search=search
    )
    photo_breakdown = AnalyticsService.photo_topic_breakdown(
        date_from=date_from, date_to=date_to, period=period, courier=courier, search=search
    )
    filter_options = AnalyticsService.get_filter_options()

    return render_template(
        "admin_analytics.html",
        reasons_data=reasons_data,
        trends_data=trends_data,
        courier_stats=courier_stats,
        status_breakdown=status_breakdown,
        photo_breakdown=photo_breakdown,
        filter_options=filter_options,
        period=period,
        date_from=date_from,
        date_to=date_to,
        courier=courier,
        status=status,
        search=search,
    )


@admin_bp.route("/weekly_report")
@admin_required
def weekly_report():
    """Еженедельный отчёт руководителя: заказы, жалобы, рейтинг курьеров, сравнение с прошлой неделей."""
    from app.services.dashboard_service import WeeklyReportService

    week = request.args.get("week", "", type=str)
    report = WeeklyReportService.week_report(week=week if week else None)

    return render_template(
        "admin_weekly_report.html",
        report=report,
        week_param=report["monday"].isoformat(),
        week_links=report["week_links"],
    )


@admin_bp.route("/weekly_report/export_excel")
@admin_required
def export_weekly_report_excel():
    """Экспорт еженедельного отчёта в Excel."""
    from app.services.dashboard_service import WeeklyReportService

    week = request.args.get("week", "", type=str)
    excel_data = WeeklyReportService.export_excel(week=week if week else None)

    monday = WeeklyReportService._parse_week_param(week if week else None)
    return send_file(
        io.BytesIO(excel_data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f'weekly_report_{monday.strftime("%Y%m%d")}.xlsx',
    )


@admin_bp.route("/photos")
@admin_required
def photo_control():
    """Страница контроля фотографий заказов."""
    from app.services.photo_service import PhotoService

    period = request.args.get("period", "week")
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    problem = request.args.get("problem", "all", type=str)
    courier = request.args.get("courier", "", type=str)
    search = request.args.get("search", "", type=str)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    # Валидация period
    if period not in PERIOD_VALUES | {"custom", "today", "week", "month"}:
        period = "week"
    if problem not in {"all", "no_photos", "required_missing", "corrupted", "duplicates"}:
        problem = "all"

    service_period = "day" if period == "today" else period
    stats = PhotoService.admin_photo_control_stats(service_period, date_from, date_to)

    pagination, orders = PhotoService.get_problematic_orders(
        service_period, date_from, date_to, problem, courier, search, page, per_page
    )

    couriers = OrderService.courier_choices()

    return render_template(
        "admin_photo_control.html",
        stats=stats,
        pagination=pagination,
        orders=orders,
        couriers=couriers,
        period=period,
        date_from=date_from,
        date_to=date_to,
        problem=problem,
        courier=courier,
        search=search,
    )


@admin_bp.route("/photos/export")
@admin_required
def export_photo_control():
    """Экспорт контроля фотографий в Excel."""
    import io

    from openpyxl import Workbook

    from app.services.photo_service import PhotoService

    period = request.args.get("period", "week")
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    problem = request.args.get("problem", "all", type=str)
    courier = request.args.get("courier", "", type=str)
    search = request.args.get("search", "", type=str)

    if period not in PERIOD_VALUES | {"custom", "today", "week", "month"}:
        period = "week"

    service_period = "day" if period == "today" else period
    _, orders = PhotoService.get_problematic_orders(
        service_period, date_from, date_to, problem, courier, search, 1, 10000
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Контроль фото"
    headers = [
        "№ п/п",
        "ID заказа",
        "Номер заказа",
        "Курьер",
        "Адрес",
        "Время доставки",
        "Подтема",
        "Кол-во фото",
        "Требуется фото",
        "Тип проблемы",
        "Файлы существуют",
    ]
    ws.append(headers)

    for idx, item in enumerate(orders, 1):
        order = item["order"]
        files_exist = all(os.path.exists(p) for p in item.get("photo_paths", []))
        ws.append(
            [
                idx,
                order.id,
                order.order_number,
                order.courier_fio or "",
                order.address or "",
                order.delivered_at or "",
                item.get("complaint_text", ""),
                item.get("photos_count", 0),
                "Да" if item.get("photo_required") else "Нет",
                item.get("problem_type", ""),
                "Да" if files_exist else "Нет",
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f'photo_control_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
    )


@admin_bp.route("/complaint/<int:complaint_id>", methods=["GET", "POST"])
@admin_required
def complaint_detail(complaint_id):
    """Детальная карточка жалобы с фото и управлением статусом."""
    from app.models.complaint import Complaint

    complaint = db.get_or_404(Complaint, complaint_id)
    # связи моделей типизированы динамически (relationship/backref), mypy их не видит
    order = cast(Any, complaint.order)
    user = order.user

    if request.method == "POST" and request.form.get("action") == "change_status":
        new_status = request.form.get("status")
        allowed_statuses = ["открыта", "в работе", "закрыта", ""]
        if new_status in allowed_statuses:
            old_status = complaint.status
            complaint.status = new_status if new_status else None
            from app.services.complaint_service import ComplaintService

            ComplaintService.sync_mirror(order)
            db.session.commit()
            flash(f'Статус изменен: {old_status or "—"} → {new_status or "—"}', "success")
            return redirect(url_for("admin.complaint_detail", complaint_id=complaint_id))
        else:
            flash("Недопустимый статус", "error")

    photo_required = any(topic in (complaint.text or "") for topic in PHOTO_REQUIRED_TOPICS)

    return render_template(
        "admin_complaint_detail.html",
        complaint=complaint,
        order=order,
        user=user,
        photos=order.photos,
        photos_count=len(order.photos),
        photo_required=photo_required,
        search=request.args.get("search", "", type=str),
        status=request.args.get("status", "", type=str),
        courier=request.args.get("courier", "", type=str),
        date_from=request.args.get("date_from", "", type=str),
        date_to=request.args.get("date_to", "", type=str),
        period=request.args.get("period", "", type=str),
    )


@admin_bp.route("/api/pending_count")
@limiter.limit("120 per minute")
def api_pending_count():
    """Живой счётчик заказов, ждущих подтверждения (для polling на дашборде)."""
    auth_error = _admin_api_auth_error()
    if auth_error is not None:
        return auth_error
    return jsonify({"pending": DashboardService.pending_orders_count()})


@admin_bp.route("/api/notifications")
@limiter.exempt
def api_notifications():
    """Центр уведомлений для дашборда руководителя.

    Возвращает вычисляемые уведомления с дедупликацией через sessionStorage на клиенте.
    """
    auth_error = _admin_api_auth_error()
    if auth_error is not None:
        return auth_error

    # Get seen keys from query param (client sends comma-separated list)
    seen_param = request.args.get("seen", "", type=str)
    seen = {}
    if seen_param:
        for part in seen_param.split(","):
            if ":" in part:
                key, ts = part.split(":", 1)
                try:
                    seen[key] = int(ts)
                except ValueError:
                    pass

    notifications = NotificationService.get_notifications(limit=20)
    filtered = NotificationService.filter_seen(notifications, seen)

    return jsonify({"notifications": filtered, "count": len(filtered)})


@admin_bp.route("/api/files")
@limiter.limit("60 per minute")
def api_files():
    """List uploaded files — used by the PC sync script.

    Auth: admin session OR X-Admin-Token header with the superadmin token.
    """
    token = request.headers.get("X-Admin-Token", "")
    if not _admin_session_valid() and not (
        token and ADMIN_TOKEN and hmac.compare_digest(token, ADMIN_TOKEN)
    ):
        return jsonify({"error": "unauthorized"}), 401

    folder = os.path.abspath(UPLOAD_FOLDER)
    files = []
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            fp = os.path.join(folder, name)
            if os.path.isfile(fp):
                files.append(
                    {
                        "name": name,
                        "size": os.path.getsize(fp),
                        "mtime": int(os.path.getmtime(fp)),
                    }
                )
    return jsonify({"files": files, "count": len(files)})


@admin_bp.route("/api/photos/restore", methods=["POST"])
@limiter.limit("60 per minute")
@csrf.exempt
def api_photos_restore():
    """Restore photos from the PC backup (scripts/push_photos.ps1).

    Body: {"files": [{"name": str, "data": base64}], "overwrite": bool}
    Auth: admin session OR X-Admin-Token header (same as /admin/api/files).
    """
    token = request.headers.get("X-Admin-Token", "")
    if not _admin_session_valid() and not (
        token and ADMIN_TOKEN and hmac.compare_digest(token, ADMIN_TOKEN)
    ):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "bad body"}), 400
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return jsonify({"error": "files must be a non-empty list"}), 400
    overwrite = bool(payload.get("overwrite"))

    uploaded = 0
    skipped = 0
    bytes_written = 0
    conflicts = []
    failed = []

    for item in files:
        if not isinstance(item, dict):
            failed.append({"name": None, "reason": "элемент должен быть объектом"})
            continue
        name = item.get("name")
        name_err = PhotoService.restore_name_error(name)
        if name_err:
            failed.append({"name": name if isinstance(name, str) else None, "reason": name_err})
            continue
        assert isinstance(name, str)  # restore_name_error отфильтровал не-str
        raw = item.get("data")
        if not isinstance(raw, str):
            failed.append({"name": name, "reason": "нет данных файла"})
            continue
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            failed.append({"name": name, "reason": "некорректный base64"})
            continue
        status, reason = PhotoService.restore_file(name, data, overwrite=overwrite)
        if status == "uploaded":
            uploaded += 1
            bytes_written += len(data)
        elif status == "skipped":
            skipped += 1
        elif status == "conflict":
            conflicts.append({"name": name, "reason": reason})
        else:
            failed.append({"name": name, "reason": reason})

    audit_logger.info("photo restore total=%s uploaded=%s", len(files), uploaded)
    return jsonify(
        {
            "uploaded": uploaded,
            "skipped": skipped,
            "bytes": bytes_written,
            "conflicts": conflicts,
            "failed": failed,
        }
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "", type=str)

    if request.method == "POST":
        fio = request.form.get("fio", "").strip()
        role = request.form.get("role", ROLE_COURIER)
        if fio:
            try:
                user = UserService.create_user(fio, role=role)
                flash(
                    f"Пользователь создан ({ROLE_LABELS.get(user.role, user.role)}). "
                    f"Токен: {user.token}",
                    "success",
                )
            except Exception:
                db.session.rollback()
                flash("Пользователь с таким ФИО уже существует", "error")
        else:
            flash("Введите ФИО", "error")
        return redirect(url_for("admin.users"))

    pagination = UserService.get_all_users(page=page, search=search)
    return render_template(
        "admin_users.html",
        users=pagination.items,
        pagination=pagination,
        search=search,
        role_labels=ROLE_LABELS,
        user_roles=USER_ROLES,
    )


@admin_bp.route("/users/role/<int:user_id>", methods=["POST"])
@admin_required
def set_user_role(user_id):
    role = request.form.get("role", "")
    if role in USER_ROLES:
        user = UserService.set_role(user_id, role)
        if user:
            flash(f"Роль обновлена: {ROLE_LABELS[role]}", "success")
    else:
        flash("Неизвестная роль", "error")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = UserService.delete_user(user_id)
    if user:
        flash("Пользователь удалён", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/regenerate_token/<int:user_id>", methods=["POST"])
@admin_required
def regenerate_token(user_id):
    user = UserService.regenerate_token(user_id)
    if user:
        flash(f"Токен обновлён. Новый токен: {user.token}", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/upload_excel", methods=["GET", "POST"])
@limiter.limit("10 per minute")
@admin_required
def upload_excel():
    if request.method == "POST":
        file = request.files.get("excel_file")
        if not file or not file.filename:
            flash("Файл не выбран", "error")
            return redirect(url_for("admin.upload_excel"))

        if file.filename.endswith(".xls"):
            flash(
                "Формат .xls не поддерживается — сохраните файл как .xlsx "
                "(Excel: Файл → Сохранить как → Книга Excel .xlsx)",
                "error",
            )
            return redirect(url_for("admin.upload_excel"))

        if not file.filename.endswith(".xlsx"):
            flash("Неверный формат файла. Нужен .xlsx", "error")
            return redirect(url_for("admin.upload_excel"))

        try:
            workbook = openpyxl.load_workbook(file)
            sheet = workbook.active

            headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]

            fio_col_idx = None
            fio_variants = ["фио", "fio", "ФИО", "Фио", "Фамилия Имя Отчество", "ФИО клиента"]
            for idx, header in enumerate(headers):
                if header and str(header).strip().lower() in [v.lower() for v in fio_variants]:
                    fio_col_idx = idx
                    break

            if fio_col_idx is None:
                flash(
                    'Не найдена колонка с ФИО. Ожидаемые названия: "ФИО", "fio", "ФИО клиента"',
                    "error",
                )
                return redirect(url_for("admin.upload_excel"))

            result = ImportService.import_orders_from_excel(file, fio_col_idx)

            msg = f'Импортировано заказов: {result["imported"]}. Пропущено строк: {result["skipped"]}. Дублей: {result["duplicates"]}.'
            if result["errors"]:
                msg += " Ошибки: " + "; ".join(result["errors"][:5])
                if len(result["errors"]) > 5:
                    msg += f' ... и ещё {len(result["errors"]) - 5} ошибок.'
            flash(msg, "success" if result["imported"] > 0 else "warning")

        except Exception as e:
            db.session.rollback()
            flash(f"Ошибка при обработке файла: {str(e)}", "error")

        return redirect(url_for("admin.upload_excel"))

    return render_template("admin_upload_excel.html")


@admin_bp.route("/import_mappings", methods=["GET", "POST"])
@admin_required
def import_mappings():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        type_ = request.form.get("type", "").strip()
        id_column = request.form.get("id_column", "").strip()
        fio_column = request.form.get("fio_column", "").strip()
        order_number_column = request.form.get("order_number_column", "").strip()

        field_mapping = {}
        keys = request.form.getlist("mapping_key[]")
        values = request.form.getlist("mapping_value[]")
        for k, v in zip(keys, values, strict=False):
            if k.strip() and v.strip():
                field_mapping[k.strip()] = v.strip()

        if not name or not type_ or not id_column:
            flash("Заполните обязательные поля: название, тип, колонка ID", "error")
        else:
            try:
                mapping = ImportMapping(
                    name=name,
                    type=type_,
                    id_column=id_column,
                    fio_column=fio_column or None,
                    order_number_column=order_number_column or None,
                    field_mapping=field_mapping,
                )
                db.session.add(mapping)
                db.session.commit()
                flash("Маппинг создан", "success")
            except Exception as e:
                db.session.rollback()
                flash(f"Ошибка: {e}", "error")
        return redirect(url_for("admin.import_mappings"))

    mappings = ImportMapping.query.order_by(ImportMapping.created_at.desc()).all()
    return render_template("admin_import_mappings.html", mappings=mappings)


@admin_bp.route("/import_mappings/delete/<int:mapping_id>", methods=["POST"])
@admin_required
def delete_import_mapping(mapping_id):
    mapping = ImportMapping.query.get_or_404(mapping_id)
    db.session.delete(mapping)
    db.session.commit()
    flash("Маппинг удалён", "success")
    return redirect(url_for("admin.import_mappings"))


@admin_bp.route("/import_excel", methods=["GET", "POST"])
@limiter.limit("10 per minute")
@admin_required
def import_excel():
    mappings = ImportMapping.query.order_by(ImportMapping.type, ImportMapping.name).all()

    if request.method == "POST":
        mapping_id = request.form.get("mapping_id", type=int)
        file = request.files.get("excel_file")

        if not mapping_id:
            flash("Выберите маппинг", "error")
            return redirect(url_for("admin.import_excel"))

        if not file or not file.filename:
            flash("Файл не выбран", "error")
            return redirect(url_for("admin.import_excel"))

        if file.filename.endswith(".xls"):
            flash("Формат .xls не поддерживается — сохраните файл как .xlsx", "error")
            return redirect(url_for("admin.import_excel"))

        if not file.filename.endswith(".xlsx"):
            flash("Неверный формат файла. Нужен .xlsx", "error")
            return redirect(url_for("admin.import_excel"))

        mapping = ImportMapping.query.get_or_404(mapping_id)
        safe_file = safe_text(file.filename)

        audit_logger.info("import start file=%s mapping=%s", safe_file, mapping.id)

        try:
            result = ImportService.import_with_mapping(file, mapping)

            # Сохраняем отчёт для скачивания только если есть ошибки
            if result.get("errors"):
                _store_import_report(
                    {
                        "file": file.filename,
                        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        "imported": result.get("imported", 0),
                        "updated": result.get("updated", 0),
                        "skipped": result.get("skipped", 0),
                        "duplicates": result.get("duplicates", 0),
                        "errors": result.get("errors", []),
                        "total_errors": len(result.get("errors", [])),
                    }
                )
            else:
                # Чистый импорт — удаляем старый отчёт (файл + указатель в сессии)
                _drop_import_report_file()
                session.pop("import_report", None)

            msg = f'Импорт завершён. Создано: {result.get("imported", 0)}, обновлено: {result.get("updated", 0)}, пропущено: {result.get("skipped", 0)}.'
            if result.get("errors"):
                msg += " Ошибки: " + "; ".join(result["errors"][:5])
                if len(result["errors"]) > 5:
                    msg += f' ... и ещё {len(result["errors"]) - 5} ошибок.'
            flash(
                msg,
                (
                    "success"
                    if (result.get("imported", 0) + result.get("updated", 0)) > 0
                    else "warning"
                ),
            )

            audit_logger.info(
                "import end file=%s mapping=%s imported=%d updated=%d skipped=%d errors=%d",
                safe_file,
                mapping.id,
                result.get("imported", 0),
                result.get("updated", 0),
                result.get("skipped", 0),
                len(result.get("errors") or []),
            )

        except Exception as e:
            db.session.rollback()
            audit_logger.warning(
                "import failed file=%s mapping=%s reason=%s",
                safe_file,
                mapping.id,
                safe_text(e),
            )
            flash(f"Ошибка при обработке файла: {str(e)}", "error")

        return redirect(url_for("admin.import_excel"))

    return render_template("admin_import_excel.html", mappings=mappings)


@admin_bp.route("/import_excel_preview", methods=["POST"])
@admin_required
def import_excel_preview():
    """Предварительный анализ файла Excel без сохранения в БД."""
    from app.services.import_service import ImportService

    mapping_id = request.form.get("mapping_id", type=int)
    file = request.files.get("excel_file")

    if not mapping_id:
        return jsonify({"error": "Выберите маппинг"}), 400

    if not file or not file.filename:
        return jsonify({"error": "Файл не выбран"}), 400

    if not file.filename.endswith(".xlsx"):
        return jsonify({"error": "Неверный формат файла. Нужен .xlsx"}), 400

    mapping = ImportMapping.query.get_or_404(mapping_id)

    try:
        preview = ImportService.preview_import(file, mapping)
        return jsonify({"preview": preview})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/import_excel_confirm", methods=["POST"])
@admin_required
def import_excel_confirm():
    """Подтверждение импорта после превью."""
    from app.services.import_service import ImportService

    mapping_id = request.form.get("mapping_id", type=int)
    file = request.files.get("excel_file")

    if not mapping_id:
        return jsonify({"error": "Выберите маппинг"}), 400

    if not file or not file.filename:
        return jsonify({"error": "Файл не выбран"}), 400

    if not file.filename.endswith(".xlsx"):
        return jsonify({"error": "Неверный формат файла. Нужен .xlsx"}), 400

    mapping = ImportMapping.query.get_or_404(mapping_id)

    try:
        result = ImportService.import_with_mapping(file, mapping)

        msg = f'Импорт завершён. Создано: {result["imported"]}, обновлено: {result["updated"]}, пропущено: {result["skipped"]}.'
        if result["errors"]:
            msg += " Ошибки: " + "; ".join(result["errors"][:5])
            if len(result["errors"]) > 5:
                msg += f' ... и ещё {len(result["errors"]) - 5} ошибок.'

        return jsonify(
            {
                "imported": result["imported"],
                "updated": result["updated"],
                "skipped": result["skipped"],
                "duplicates": result["duplicates"],
                "errors": result["errors"],
                "message": msg,
            }
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/import_report")
@admin_required
def import_report():
    """Скачивание отчёта об ошибках последнего импорта.

    Указатель на отчёт хранится в сессии, сам файл — на диске (см.
    _store_import_report). Если отчёта нет — редирект на страницу импорта.
    """
    report = _pop_import_report()
    if not report:
        return redirect(url_for("admin.import_excel"))

    # Формируем текстовый отчёт
    errors = report.get("errors", [])
    total_errors = report.get("total_errors") or len(errors)
    lines = []
    lines.append(f"Файл: {report.get('file', '—')}")
    lines.append(f"Дата: {report.get('generated_at', '—')}")
    lines.append(
        f"Создано: {report.get('imported', 0)}, обновлено: {report.get('updated', 0)}, пропущено: {report.get('skipped', 0)}"
    )
    lines.append(f"Дублей: {report.get('duplicates', 0)}")
    lines.append(f"Всего ошибок: {total_errors}")
    lines.append("")

    # Показываем первые 1000 ошибок + сводку
    for i, error in enumerate(errors[:1000]):
        lines.append(f"{i + 1}. {error}")
    if total_errors > 1000:
        lines.append(f"... и ещё {total_errors - 1000} ошибок")

    output = "\n".join(lines)
    return send_file(
        io.BytesIO(output.encode("utf-8-sig")),
        mimetype="text/plain; charset=utf-8",
        as_attachment=True,
        download_name=f'import_errors_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt',
    )


@admin_bp.route("/orders")
@admin_required
def orders():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("search", "", type=str)
    user_filter = request.args.get("user_id", "", type=str)
    photo_filter = request.args.get("photo_filter", "", type=str)
    courier = request.args.get("courier", "", type=str)
    complaint_filter = request.args.get("complaint_filter", "", type=str)
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    period = request.args.get("period", "", type=str)

    # Валидация: user_id должен быть числом, иначе фильтр игнорируется (раньше → 500)
    if user_filter and not user_filter.isdigit():
        user_filter = ""

    pagination, orders_with_users = OrderService.get_all_orders(
        page=page,
        search=search,
        user_filter=user_filter,
        photo_filter=photo_filter,
        courier=courier,
        complaint_filter=complaint_filter,
        date_from=date_from,
        date_to=date_to,
        period=period,
    )

    users = UserService.get_all_users(per_page=1000).items

    return render_template(
        "admin_orders.html",
        orders=orders_with_users,
        pagination=pagination,
        search=search,
        users=users,
        user_filter=user_filter,
        photo_filter=photo_filter,
        courier=courier,
        complaint_filter=complaint_filter,
        date_from=date_from,
        date_to=date_to,
        period=period,
    )


@admin_bp.route("/orders/send_email/<int:order_id>", methods=["POST"])
@admin_required
def send_order_email(order_id):
    result = OrderService.get_order_with_user(order_id)
    if not result:
        flash("Заказ не найден", "error")
        return redirect(url_for("admin.orders"))

    order, user = result

    if not EmailService.is_configured():
        flash(
            "Почта не настроена: задайте MAIL_USERNAME, MAIL_PASSWORD и "
            "MAIL_ADMIN_RECIPIENTS в настройках сервера",
            "warning",
        )
    elif EmailService.send_order_completion_email(order, user):
        flash("Email отправлен администраторам", "success")
    else:
        flash("Ошибка отправки письма — подробности в логах сервера (logs/app.log)", "error")

    return redirect(url_for("admin.orders"))


@admin_bp.route("/orders/delete/<int:order_id>", methods=["POST"])
@admin_required
def delete_order(order_id):
    if OrderService.delete_order(order_id):
        flash("Заказ удалён", "success")
    return redirect(url_for("admin.orders"))


@admin_bp.route("/orders/edit/<int:order_id>", methods=["GET", "POST"])
@admin_required
def edit_order(order_id):
    order = OrderService.get_order_by_id(order_id)
    if not order:
        flash("Заказ не найден", "error")
        return redirect(url_for("admin.orders"))

    if request.method == "POST":
        OrderService.update_order(
            order_id, request.form.get("order_number", ""), request.form.get("description", "")
        )
        flash("Заказ обновлён", "success")
        return redirect(url_for("admin.orders"))

    return render_template("admin_edit_order.html", order=order)


@admin_bp.route("/orders/<int:order_id>/photos")
@admin_required
def order_photos(order_id):
    order = OrderService.get_order_by_id(order_id)
    if not order:
        flash("Заказ не найден", "error")
        return redirect(url_for("admin.orders"))

    photos = PhotoService.get_order_photos(order_id)
    # Человеческое описание + служебные поля импорта отдельным блоком (F-05)
    order.summary, order.technical_description = split_description(order.description)
    return render_template("user_order_photos.html", order=order, photos=photos, admin_view=True)


@admin_bp.route("/orders/<int:order_id>/photos/delete/<int:photo_id>", methods=["POST"])
@admin_required
def delete_photo(order_id, photo_id):
    if PhotoService.delete_photo_admin(photo_id, order_id):
        flash("Фото удалено", "success")
    else:
        flash("Фото не найдено", "error")
    return redirect(url_for("admin.order_photos", order_id=order_id))


@admin_bp.route("/export_excel")
@admin_required
def export_excel():
    from sqlalchemy import func

    from app.models import Complaint, Order, Photo

    wb = Workbook()
    ws = wb.active
    ws.title = "Заказы"

    headers = [
        "ID",
        "ФИО",
        "Курьер",
        "Номер заказа",
        "Описание",
        "Создан",
        "Кол-во фото",
        "Курьер",
        "Доставлен",
        "Статус жалобы",
        "Кол-во жалоб",
    ]
    ws.append(headers)

    # Используем те же фильтры, что и в списке заказов
    search = request.args.get("search", "", type=str)
    user_filter = request.args.get("user_id", "", type=str)
    photo_filter = request.args.get("photo_filter", "", type=str)
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    period = request.args.get("period", "", type=str)
    courier = request.args.get("courier", "", type=str)
    complaint_filter = request.args.get("complaint_filter", "", type=str)

    # Валидация user_id
    if user_filter and not user_filter.isdigit():
        user_filter = ""

    query = OrderService.build_orders_query(
        search=search,
        user_filter=user_filter,
        photo_filter=photo_filter,
        date_from=date_from,
        date_to=date_to,
        period=period,
        courier=courier,
        complaint_filter=complaint_filter,
    )

    orders = query.order_by(Order.created_at.desc(), Order.id.desc()).all()

    # Предварительный расчет фото для не N+1
    order_ids = [order.id for order, _ in orders]
    photo_counts = {}
    complaint_counts = {}
    complaint_statuses = {}
    if order_ids:
        photo_counts = dict(
            db.session.query(Photo.order_id, func.count(Photo.id))
            .filter(Photo.order_id.in_(order_ids))
            .group_by(Photo.order_id)
            .all()
        )
        complaint_counts = dict(
            db.session.query(Complaint.order_id, func.count(Complaint.id))
            .filter(Complaint.order_id.in_(order_ids))
            .group_by(Complaint.order_id)
            .all()
        )
        complaint_statuses = dict(
            db.session.query(Complaint.order_id, Complaint.status)
            .filter(Complaint.order_id.in_(order_ids), Complaint.status.isnot(None))
            .order_by(Complaint.created_at.desc())
            .all()
        )

    for order, user in orders:
        ws.append(
            [
                order.id,
                user.fio,
                order.courier_fio or "",
                order.order_number,
                order.description,
                order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
                photo_counts.get(order.id, 0),
                order.courier_fio or "",
                order.delivered_at or "",
                complaint_statuses.get(order.id, "") or "",
                complaint_counts.get(order.id, 0),
            ]
        )

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f'orders_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
    )


@admin_bp.route("/audit")
@admin_required
def audit():
    """История изменений (аудит) для администратора."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    action_type = request.args.get("action_type", "", type=str)
    object_type = request.args.get("object_type", "", type=str)
    object_id = request.args.get("object_id", "", type=str)
    user_id = request.args.get("user_id", "", type=str)
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    search = request.args.get("search", "", type=str)

    # Валидация per_page
    if per_page not in (25, 50, 100):
        per_page = 50

    # Convert user_id to int if provided
    user_id_int = int(user_id) if user_id.isdigit() else None

    pagination, rows = AuditService.get_logs(
        page=page,
        per_page=per_page,
        action_type=action_type or None,
        object_type=object_type or None,
        object_id=object_id or None,
        user_id=user_id_int,
        date_from=date_from or None,
        date_to=date_to or None,
        search=search or None,
    )

    filter_options = AuditService.get_filter_options()
    stats = AuditService.get_stats()

    return render_template(
        "admin_audit.html",
        pagination=pagination,
        rows=rows,
        action_type=action_type,
        object_type=object_type,
        object_id=object_id,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
        search=search,
        per_page=per_page,
        filter_options=filter_options,
        stats=stats,
    )


# ==================== Операционный анализ ====================


@admin_bp.route("/operational_analysis")
@admin_required
def operational_analysis():
    """Список сессий операционного анализа + форма загрузки."""
    from app.services.operational_analysis_service import OperationalAnalysisService

    sessions = OperationalAnalysisService.get_recent_sessions(limit=20)
    return render_template("admin_operational_analysis.html", sessions=sessions)


@admin_bp.route("/operational_analysis/upload", methods=["POST"])
@admin_required
def operational_analysis_upload():
    """Загрузка и обработка Excel-файла с тремя листами."""
    from app.services.operational_analysis_service import OperationalAnalysisService

    file = request.files.get("excel_file")
    if not file or not file.filename:
        flash("Файл не выбран", "error")
        return redirect(url_for("admin.operational_analysis"))

    if file.filename.endswith(".xls"):
        flash("Формат .xls не поддерживается — сохраните файл как .xlsx", "error")
        return redirect(url_for("admin.operational_analysis"))

    if not file.filename.endswith(".xlsx"):
        flash("Неверный формат файла. Нужен .xlsx", "error")
        return redirect(url_for("admin.operational_analysis"))

    wait_threshold = request.form.get("wait_threshold", type=int)
    if wait_threshold is None or wait_threshold < 0:
        wait_threshold = OperationalAnalysisService.DEFAULT_WAIT_THRESHOLD

    try:
        session_obj = OperationalAnalysisService.analyze_file(file, wait_threshold=wait_threshold)
        flash(
            f"Анализ завершён. Заказов: {session_obj.total_orders}, "
            f"жжлоб: {session_obj.total_complaints}, "
            f"собрано: {session_obj.total_assembly_orders}. "
            f"Ошибок: {session_obj.error_count}, предупреждений: {session_obj.warning_count}.",
            "success",
        )
        return redirect(url_for("admin.operational_analysis_results", session_id=session_obj.id))
    except Exception as e:
        db.session.rollback()
        flash(f"Ошибка при обработке файла: {str(e)}", "error")
        return redirect(url_for("admin.operational_analysis"))


@admin_bp.route("/operational_analysis/<int:session_id>")
@admin_required
def operational_analysis_results(session_id):
    """Просмотр результатов операционного анализа."""
    from app.services.operational_analysis_service import OperationalAnalysisService

    picker = request.args.get("picker", "", type=str)
    date_from = request.args.get("date_from", "", type=str)
    date_to = request.args.get("date_to", "", type=str)
    time_of_day = request.args.get("time_of_day", "", type=str)
    has_complaint = request.args.get("has_complaint", "", type=str)

    if any([picker, date_from, date_to, time_of_day, has_complaint]):
        results = OperationalAnalysisService.filter_results(
            session_id,
            picker=picker,
            date_from=date_from,
            date_to=date_to,
            time_of_day=time_of_day,
            has_complaint=has_complaint,
        )
    else:
        results = OperationalAnalysisService.get_session_results(session_id)

    if not results:
        flash("Сессия анализа не найдена", "error")
        return redirect(url_for("admin.operational_analysis"))

    return render_template(
        "admin_operational_analysis_results.html",
        results=results,
        picker=picker,
        date_from=date_from,
        date_to=date_to,
        time_of_day=time_of_day,
        has_complaint=has_complaint,
    )


@admin_bp.route("/operational_analysis/<int:session_id>/export")
@admin_required
def operational_analysis_export(session_id):
    """Экспорт результатов операционного анализа в Excel."""
    from app.services.operational_analysis_service import OperationalAnalysisService

    excel_data = OperationalAnalysisService.export_excel(session_id)
    if not excel_data:
        flash("Данные для экспорта не найдены", "error")
        return redirect(url_for("admin.operational_analysis"))

    return send_file(
        io.BytesIO(excel_data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f'operational_analysis_{session_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
    )


@admin_bp.route("/operational_analysis/delete/<int:session_id>", methods=["POST"])
@admin_required
def operational_analysis_delete(session_id):
    """Удаление сессии операционного анализа."""
    from app.services.operational_analysis_service import OperationalAnalysisService

    if OperationalAnalysisService.delete_session(session_id):
        flash("Сессия анализа удалена", "success")
    else:
        flash("Сессия не найдена", "error")
    return redirect(url_for("admin.operational_analysis"))
