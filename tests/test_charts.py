"""Регрессии графиков «Заказы по дням» / «Жалобы по дням» (2026-10-13).

Дефекты, из-за которых графики дашборда и недельного отчёта были пустыми:

- **D-1** `admin_dashboard.html`: накопитель `points_str` объявлялся через
  `{% set %}` внутри `{% for %}`. В Jinja2 такое присваивание живёт только в
  scope цикла, поэтому после цикла `points_str` снова `''` и
  `{% if points_str %}` было ложно — `<polyline>` не рендерился никогда.
  Случайно «работала» только ветка одной точки (`<circle>` без цикла).
- **D-2** `DashboardService.admin_stats()` (источник данных `/admin/`) не
  возвращал `daily_complaints`, хотя шаблон читает `stats.daily_complaints`
  («Жалобы по дням» всегда показывали empty-state).
- **D-3** route `admin.dashboard` не читал `period`/`date_from`/`date_to` —
  селектор периода не фильтровал, а ссылки теряли выбранный период.
- **D-4** кнопка «Сегодня» слала `period="today"`, тогда как `period_bounds()`
  распознаёт только `day`/`week`/`month`.
- **D-5** `admin_weekly_report.html`: тот же Jinja2 scoping-баг в обоих блоках.

Старые тесты (`test_ux_fixes.py::TestDashboardChart`) проверяли только
отсутствие заглушки «в разработке» и рендерили ровно одну точку, поэтому баг
не ловился.
"""

from datetime import datetime, timedelta
from datetime import time as dtime
from types import SimpleNamespace

import pytest
from flask import render_template, render_template_string

from app.models.complaint import Complaint
from app.models.order import Order


def _day(dt):
    """Дата строкой YYYY-MM-DD, как её отдаёт func.date() в SQLite."""
    return dt.strftime("%Y-%m-%d")


def _days_ago(n):
    return datetime.utcnow() - timedelta(days=n)


def _chart_card(html, index):
    """Вырезает n-й `<div class='card chart-card'>` из HTML."""
    blocks = html.split("<div class='card chart-card'>")
    assert len(blocks) > index + 1, "в HTML меньше chart-card, чем ожидалось"
    return blocks[index + 1]


def _base_stats(**overrides):
    """stats для рендера admin_dashboard.html (все ключи, что читает шаблон)."""
    stats = {
        "users_count": 0,
        "couriers_count": 0,
        "orders_count": 0,
        "photos_count": 0,
        "orders_with_photos": 0,
        "orders_without_photos": 0,
        "pending_count": 0,
        "orders_with_complaints": 0,
        "open_complaints_count": 0,
        "import_errors_count": 0,
        "mirror_only_count": 0,
        "completion_rate": 0,
        "pending_orders": [],
        "daily_orders": [],
        "daily_complaints": [],
        "best_couriers": [],
        "best_pickers": [],
        "avg_courier_score": None,
        "attention_total": 0,
        "upload_size": 0,
    }
    stats.update(overrides)
    return stats


def _shape(items):
    """Список объектов нужной формы (day/count) для спарклайна."""
    return [SimpleNamespace(day=d, count=c) for d, c in items]


@pytest.fixture
def chart_data(db_session):
    """Фабрика заказов/жалоб с авто-очисткой.

    БД тестовая сессионная и общая, поэтому посторонние заказы ломают
    `test_order_service.py::test_get_all_orders` (там `total == len(numbers)`
    при per_page=100). Фикстура удаляет ровно то, что создала.
    """
    order_ids: list[int] = []
    complaint_ids: list[int] = []

    def add_order(user, number, created_at):
        order = Order(
            user_id=user.id, order_number=number, address=f"Адрес {number}", created_at=created_at
        )
        db_session.add(order)
        db_session.flush()
        order_ids.append(order.id)
        return order

    def add_complaint(order, created_at, text="Товар побит/вскрыт", order_id=None):
        complaint = Complaint(
            order_id=order_id if order_id is not None else order.id,
            text=text,
            status="открыта",
            created_at=created_at,
        )
        db_session.add(complaint)
        db_session.flush()
        complaint_ids.append(complaint.id)
        return complaint

    yield SimpleNamespace(order=add_order, complaint=add_complaint)

    db_session.query(Complaint).filter(Complaint.id.in_(complaint_ids)).delete(
        synchronize_session=False
    )
    db_session.query(Order).filter(Order.id.in_(order_ids)).delete(synchronize_session=False)
    db_session.commit()


class TestDashboardDailyOrdersChart:
    """D-1: спарклайн с 2+ точками должен рисовать polyline."""

    def test_three_points_render_polyline(self, app):
        with app.test_request_context():
            html = render_template(
                "admin_dashboard.html",
                stats=_base_stats(
                    daily_orders=_shape([("2026-10-10", 3), ("2026-10-11", 5), ("2026-10-12", 2)])
                ),
                notifications=[],
            )
        card = _chart_card(html, 0)
        assert "<polyline" in card, "при 3 точках должен рендериться polyline"
        assert "0.0," in card and "50.0," in card and "100.0," in card
        assert "points='0.0," in card, "points не должен быть пустым"

    def test_two_points_render_polyline(self, app):
        with app.test_request_context():
            html = render_template(
                "admin_dashboard.html",
                stats=_base_stats(daily_orders=_shape([("2026-10-11", 4), ("2026-10-12", 1)])),
                notifications=[],
            )
        assert "<polyline" in _chart_card(html, 0)

    def test_single_point_renders_circle(self, app):
        """Прежнее поведение (P3-UX-2) не должно сломаться."""
        with app.test_request_context():
            html = render_template(
                "admin_dashboard.html",
                stats=_base_stats(daily_orders=_shape([("2026-10-12", 3)])),
                notifications=[],
            )
        card = _chart_card(html, 0)
        assert "<circle" in card
        assert "r='2.5'" in card
        assert "<polyline" not in card

    def test_no_points_shows_empty_state(self, app):
        with app.test_request_context():
            html = render_template("admin_dashboard.html", stats=_base_stats(), notifications=[])
        card = _chart_card(html, 0)
        assert "Нет данных за период" in card
        assert "<polyline" not in card
        assert "<circle" not in card

    def test_real_db_rows_render_polyline(self, admin_client, db_session, regular_user, chart_data):
        """Рендер настоящих SQLAlchemy Row (атрибутивный доступ .day/.count)."""
        for offset, number in [(3, "CH-3"), (2, "CH-2"), (1, "CH-1"), (0, "CH-0")]:
            chart_data.order(regular_user, number, _days_ago(offset))
        db_session.commit()

        card = _chart_card(admin_client.get("/admin/").get_data(as_text=True), 0)
        assert "<polyline" in card, "по реальным данным за 4 дня polyline обязан быть"


class TestDashboardDailyComplaintsChart:
    """D-2: admin_stats() должен отдавать daily_complaints."""

    def test_admin_stats_provides_daily_complaints(self, app):
        from app.services.dashboard_service import DashboardService

        stats = DashboardService.admin_stats()
        assert "daily_complaints" in stats, "шаблон читает stats.daily_complaints"
        assert "daily_orders" in stats, "ключ daily_orders не должен потеряться"

    def test_complaints_render_polyline(self, admin_client, db_session, regular_user, chart_data):
        """Жалобы за два дня -> polyline с 2 точками в «Жалобы по дням»."""
        for offset, number in [(1, "CMP-1"), (0, "CMP-0")]:
            order = chart_data.order(regular_user, number, _days_ago(offset))
            chart_data.complaint(order, _days_ago(offset))
        db_session.commit()

        card = _chart_card(admin_client.get("/admin/").get_data(as_text=True), 1)
        assert "Нет жалоб за период" not in card, "жалобы есть, empty-state не нужен"
        assert "<polyline" in card, "при жалобах за 2 дня polyline обязан быть"
        assert "points='0.0," in card

    def test_daily_complaints_values_match_db(self, app, db_session, regular_user, chart_data):
        """Свои жалобы попадают в график (счётчик >=1: БД тестовая общая)."""
        from app.services.dashboard_service import DashboardService

        for offset, number in [(2, "DC-2"), (0, "DC-0")]:
            order = chart_data.order(regular_user, number, _days_ago(offset))
            chart_data.complaint(order, _days_ago(offset))
        db_session.commit()

        rows = {
            str(day): count for day, count in DashboardService.admin_stats()["daily_complaints"]
        }
        assert rows.get(_day(_days_ago(2)), 0) >= 1
        assert rows.get(_day(_days_ago(0)), 0) >= 1

    def test_complaint_older_than_week_excluded(self, app, db_session, regular_user, chart_data):
        """Окно графика — 7 дней: старая жалоба не должна попадать."""
        from app.services.dashboard_service import DashboardService

        for offset, number in [(0, "OLD-0"), (30, "OLD-30")]:
            order = chart_data.order(regular_user, number, _days_ago(offset))
            chart_data.complaint(order, _days_ago(offset))
        db_session.commit()

        days = {str(day) for day, _ in DashboardService.admin_stats()["daily_complaints"]}
        assert _day(_days_ago(0)) in days
        assert _day(_days_ago(30)) not in days

    def test_orphan_complaints_not_counted(self, app, db_session, regular_user, chart_data):
        """Жалоба удалённого заказа (сирота) не должна влиять на график."""
        from app.services.dashboard_service import DashboardService

        order = chart_data.order(regular_user, "ORPH-0", _days_ago(0))
        chart_data.complaint(order, _days_ago(0))
        db_session.commit()

        def _count_for_today():
            rows = {
                str(day): count for day, count in DashboardService.admin_stats()["daily_complaints"]
            }
            return rows.get(_day(_days_ago(0)), 0)

        before = _count_for_today()

        # симулируем сироту: order_id указывает на несуществующий заказ
        chart_data.complaint(None, _days_ago(0), order_id=999999)
        db_session.commit()

        assert _count_for_today() == before, "сирота не должна учитываться в графике"

    def test_no_complaints_shows_empty_state(self, app):
        """Рендер через шаблон: empty-state вместо пустого SVG."""
        with app.test_request_context():
            html = render_template(
                "admin_dashboard.html",
                stats=_base_stats(daily_orders=_shape([("2026-10-12", 2)])),
                notifications=[],
            )
        card = _chart_card(html, 1)
        assert "Нет жалоб за период" in card
        assert "<polyline" not in card
        assert "<circle" not in card


class TestDashboardPeriodFilter:
    """D-3/D-4: селектор периода дашборда."""

    def test_period_propagates_to_orders_links(self, admin_client):
        html = admin_client.get("/admin/?period=week").get_data(as_text=True)
        assert "admin/orders?photo_filter=with_photos&amp;period=week" in html

    def test_period_week_button_active(self, admin_client):
        html = admin_client.get("/admin/?period=week").get_data(as_text=True)
        assert "period-btn is-active'>" in html

    def test_today_button_uses_day_code(self, admin_client):
        """D-4: «Сегодня» должна слать period=day (не today), иначе фильтр мёртв."""
        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "period=day" in html
        assert "period=today" not in html

    def test_today_button_active(self, admin_client):
        html = admin_client.get("/admin/?period=day").get_data(as_text=True)
        start = html.find("period-selector")
        assert html.find("period-btn is-active'", start) > start

    def test_custom_dates_mark_period(self, admin_client):
        html = admin_client.get(
            "/admin/?period=custom&date_from=2026-10-01&date_to=2026-10-05"
        ).get_data(as_text=True)
        # шаблон использует одинарные кавычки: value='{{ date_to }}'
        assert "value='2026-10-01'" in html
        assert "value='2026-10-05'" in html

    def test_period_month_filters_out_old_orders(self, app, db_session, regular_user, chart_data):
        """D-3: период реально фильтрует выборку."""
        from app.services.dashboard_service import DashboardService

        chart_data.order(regular_user, "PF-0", _days_ago(0))
        chart_data.order(regular_user, "PF-40", _days_ago(40))  # вне месяца
        db_session.commit()

        days = {str(day) for day, _ in DashboardService.admin_stats_period("month")["daily_orders"]}
        assert _day(_days_ago(0)) in days
        assert _day(_days_ago(40)) not in days

    def test_period_stats_has_both_charts(self, app):
        from app.services.dashboard_service import DashboardService

        stats = DashboardService.admin_stats_period("week")
        assert "daily_orders" in stats
        assert "daily_complaints" in stats

    def test_no_period_uses_default_stats(self, admin_client):
        """Без параметров поведение не меняется (admin_stats, не period)."""
        assert admin_client.get("/admin/").status_code == 200


class TestWeeklyReportCharts:
    """D-5: графики недельного отчёта."""

    def _monday_of_current_week(self):
        today = datetime.utcnow().date()
        return today - timedelta(days=today.weekday())

    def test_both_charts_render_polyline(self, admin_client, db_session, regular_user, chart_data):
        monday = self._monday_of_current_week()
        for delta, number in [(0, "WR-1"), (1, "WR-2")]:
            day = datetime.combine(monday + timedelta(days=delta), dtime(10, 0))
            order = chart_data.order(regular_user, number, day)
            chart_data.complaint(order, day)
        db_session.commit()

        html = admin_client.get("/admin/weekly_report").get_data(as_text=True)
        assert html.count("<polyline") >= 2, "оба графика (заказы и жалобы) должны быть"
        assert 'points="0.0,' in html

    def test_old_week_shows_empty_state(self, admin_client):
        """Неделя 2019-06-03 (пн) заведомо без данных -> пустые состояния."""
        html = admin_client.get("/admin/weekly_report?week=2019-06-03").get_data(as_text=True)
        assert "Нет данных за период" in html
        assert "Нет жалоб за период" in html


class TestJinjaScopingRegression:
    """Прямая проверка корневой причины D-1/D-5 (Jinja2 for-scope)."""

    def test_set_inside_for_does_not_leak(self, app):
        """Документируем поведение Jinja2, на котором спотыкались."""
        with app.test_request_context():
            out = render_template_string(
                "{% set acc = '' %}"
                "{% for i in [1, 2, 3] %}{% set acc = acc ~ i %}{% endfor %}"
                "acc=[{{ acc }}]"
            )
        assert "acc=[]" in out

    def test_namespace_accumulator_works(self, app):
        with app.test_request_context():
            out = render_template_string(
                "{% set ns = namespace(points='') %}"
                "{% for i in [1, 2, 3] %}{% set ns.points = ns.points ~ i %}{% endfor %}"
                "ns=[{{ ns.points }}]"
            )
        assert "ns=[123]" in out
