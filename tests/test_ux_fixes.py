"""Регрессии визуальных/UX-дефектов аудита 2026-10-12 (F-05, P2-UX-1, P3-UX-*).

Дефекты зафиксированы скриншотами живого сайта в
docs/audit/GL_JB_UX_UI_AUDIT.md. Здесь проверяются как отрендеренный HTML,
так и исходники статики/шаблонов (JS и CSS pytest напрямую не исполняются).
"""

import re
from pathlib import Path
from types import SimpleNamespace

from app.services.order_service import OrderService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC = PROJECT_ROOT / "app" / "static"
TEMPLATES = PROJECT_ROOT / "app" / "templates"


class TestDescriptionRendering:
    """F-05: списки показывают человекочитаемое описание, не служебные поля."""

    def test_orders_list_uses_summary(self, admin_client, db_session, regular_user):
        from app.models.order import Order

        order = Order(
            user_id=regular_user.id,
            order_number="UX-1",
            address="Бишкек, Киевская 10",
            description="czf: A; sla: 3",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get("/admin/orders").get_data(as_text=True)
        assert "Бишкек, Киевская 10" in html
        assert "czf: A" not in html

    def test_photo_control_list_uses_summary(self, admin_client, db_session, regular_user):
        from app.models.order import Order

        order = Order(
            user_id=regular_user.id,
            order_number="UX-2",
            address="Ош, Масалиева 5",
            complaint_text="Товар побит/вскрыт",
            description="confirmed_at: 1.1.26",
        )
        db_session.add(order)
        db_session.commit()

        resp = admin_client.get("/admin/photos", query_string={"period": "month"})
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Ош, Масалиева 5" in html
        assert "confirmed_at" not in html

    def test_order_photos_page_shows_technical_block(self, admin_client, db_session, regular_user):
        from app.models.order import Order

        order = Order(
            user_id=regular_user.id,
            order_number="UX-3",
            address="Бишкек, Мира 7",
            description="czf: МОК Бишкек 14:1; sla: 15",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get(f"/admin/orders/{order.id}/photos").get_data(as_text=True)
        assert "Технические данные заказа" in html
        assert "czf: МОК Бишкек 14:1" in html


class TestOrdersTableColumns:
    """P2-UX-1: ID и даты не рвутся по одной цифре на строку."""

    def test_css_has_nowrap_column_rules(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        assert re.search(r"\.table th\[class\*=\"col-\"\]", css)
        assert "white-space: nowrap" in css
        assert ".table .badge" in css

    def test_orders_table_marks_id_and_date(self):
        html = (TEMPLATES / "admin_orders.html").read_text(encoding="utf-8")
        assert 'class="col-id"' in html
        assert 'class="col-date"' in html

    def test_photo_control_table_marks_id_and_date(self):
        html = (TEMPLATES / "admin_photo_control.html").read_text(encoding="utf-8")
        assert "class='col-id'" in html
        assert "class='col-date'" in html

    def test_users_table_marks_date(self):
        html = (TEMPLATES / "admin_users.html").read_text(encoding="utf-8")
        assert 'class="col-date"' in html


class TestDashboardChart:
    """P3-UX-2: при одной точке рисуется круг; P3-UX-3: жалбы по дням — данные."""

    def test_upload_path_not_shown(self, admin_client):
        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "/app/uploads" not in html
        assert "upload_folder" not in html

    def test_daily_complaints_in_stats(self, admin_client):
        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "в разработке" not in html

    def test_sparkline_single_point_renders_circle(self, app):
        with app.test_request_context():
            from flask import render_template

            html = render_template(
                "admin_dashboard.html",
                stats={
                    "daily_orders": [SimpleNamespace(day="2026-10-06", count=3)],
                    "daily_complaints": [SimpleNamespace(day="2026-10-06", count=1)],
                    "pending_orders": [],
                    "best_couriers": [],
                    "best_pickers": [],
                    "notifications": [],
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
                    "upload_size": 0,
                    "avg_courier_score": None,
                    "attention_total": 0,
                },
                notifications=[],
            )
        assert "<circle" in html, "Одиночная точка должна рисоваться кругом"


class TestFileInputLocalization:
    """P3-UX-5: нативный file input скрыт, показывает русскую подпись."""

    def test_css_has_file_input_styles(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        assert '.file-input input[type="file"]' in css
        assert ".file-input-name" in css

    def test_utils_has_file_input_initializer(self):
        js = (STATIC / "utils.js").read_text(encoding="utf-8")
        assert "GLB.initFileInputs" in js
        assert "Файл не выбран" in js

    def test_templates_use_custom_file_input(self):
        for name in (
            "admin_upload_excel.html",
            "admin_import_excel.html",
            "admin_operational_analysis.html",
        ):
            html = (TEMPLATES / name).read_text(encoding="utf-8")
            assert "data-file-input" in html, f"{name}: нет data-file-input"
            assert "data-file-name" in html, f"{name}: нет data-file-name"
            assert "Выберите файл…" in html, f"{name}: нет русской подписи кнопки"


class TestSwipe:
    """P3-UX-1: тач-цели на мобильных >= 44px."""

    def test_theme_btn_min_height_44_on_mobile(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        assert re.search(
            r"@media \(max-width: 768px\) \{[^}]*theme-btn[^}]*min-height: 44px",
            css,
            re.DOTALL,
        ), "Мобильный блок .theme-btn без min-height: 44px"

    def test_btn_sm_min_height_44_on_mobile(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        assert re.search(
            r"@media \(max-width: 768px\) \{[^}]*\.btn-sm[^}]*min-height: 44px", css, re.DOTALL
        )


class TestErrorPageActions:
    """P3-UX-7: на странице 500 есть «Повторить» и «Назад», не только «На главную»."""

    def test_500_page_has_repeat_and_back(self):
        html = (TEMPLATES / "errors" / "500.html").read_text(encoding="utf-8")
        assert "location.reload()" in html
        assert "history.back()" in html
        assert "url_for('main.index')" in html


class TestSummaryInServiceItems:
    """Сервисы отдают готовое человекочитаемое описание в items."""

    def test_get_all_orders_item_has_summary(self, db_session, regular_user):
        from app.models.order import Order

        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="SUM-ITEM",
                address="Бишкек, Ахунбаева 1",
            )
        )
        db_session.commit()
        _pagination, items = OrderService.get_all_orders(page=1, per_page=10)
        assert all("summary" in item for item in items)
        assert any("Ахунбаева" in item["summary"] for item in items)

    def test_photo_control_item_has_summary(self, db_session, regular_user):
        from app.models.order import Order
        from app.services.photo_service import PhotoService

        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="SUM-PC",
                address="Бишкек, Жукеева 2",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()
        _pagination, items = PhotoService.get_problematic_orders(
            "month", "", "", "all", "", "", 1, 10
        )
        assert items, "Ожидался хотя бы один проблемный заказ"
        assert all("summary" in item for item in items)
