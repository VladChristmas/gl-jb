import io

import openpyxl
import pytest

from app.models import ImportMapping, Order
from app.services.dashboard_service import DashboardService
from app.services.import_service import ImportService


@pytest.fixture(autouse=True)
def clean_orders(db_session):
    """Isolate from leftover orders created by other tests (session-scoped DB)."""
    Order.query.delete()
    ImportMapping.query.delete()
    db_session.commit()
    yield


def make_file(headers, rows):
    """Helper to create Excel file in memory."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


COURIER_PICKER_HEADERS = [
    "ID",
    "Номер заказа",
    "Курьер",
    "Адрес",
    "Доставлено",
    "Сборщик",
    "Количество собранные заказы",
    "Время ожидания",
    "Скорость сборки",
]


class TestImportCourierPickerFields:
    def test_import_with_mapping_extracts_fields(self, db_session, regular_user):
        mapping = ImportMapping(
            name="orders-full",
            type="orders",
            id_column="ID",
            fio_column="Курьер",
            order_number_column="Номер заказа",
            field_mapping={},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = make_file(
            COURIER_PICKER_HEADERS,
            [
                [
                    "101",
                    "ORD-1",
                    regular_user.fio,
                    "ул. Ленина, 1",
                    "05.10.2026 14:00",
                    "Иванов Иван",
                    "42",
                    "5 мин",
                    "12/час",
                ]
            ],
        )

        result = ImportService.import_with_mapping(file, mapping)
        assert result["imported"] == 1

        order = Order.query.filter_by(external_id="101").first()
        assert order is not None
        assert order.courier_fio == regular_user.fio
        assert order.address == "ул. Ленина, 1"
        assert order.delivered_at == "05.10.2026 14:00"
        assert order.picker_fio == "Иванов Иван"
        assert order.pick_count == 42
        assert order.wait_time == "5 мин"
        assert order.pick_speed == "12/час"

    def test_simple_import_extracts_fields(self, db_session, regular_user):
        file = make_file(
            ["ФИО", "Номер заказа", "Курьер", "Адрес", "Сборщик"],
            [[regular_user.fio, "ORD-2", regular_user.fio, "пр. Мира, 5", "Сидоров С."]],
        )

        result = ImportService.import_orders_from_excel(file, fio_col_idx=0)
        assert result["imported"] == 1

        order = Order.query.filter_by(order_number="ORD-2").first()
        assert order is not None
        assert order.courier_fio == regular_user.fio
        assert order.address == "пр. Мира, 5"
        assert order.picker_fio == "Сидоров С."

    def test_bad_pick_count_ignored(self, db_session, regular_user):
        file = make_file(
            ["ФИО", "Номер заказа", "Количество собранные заказы"],
            [[regular_user.fio, "ORD-3", "не число"]],
        )
        ImportService.import_orders_from_excel(file, fio_col_idx=0)
        order = Order.query.filter_by(order_number="ORD-3").first()
        assert order is not None
        assert order.pick_count is None

    def test_complaint_fallback_matches_by_order_number(self, db_session, regular_user):
        order = Order(user_id=regular_user.id, order_number="ORD-555")
        db_session.add(order)
        db_session.commit()

        mapping = ImportMapping(
            name="complaints-by-number",
            type="complaints",
            id_column="ID заказа",
            field_mapping={"Жалоба": "complaint_text"},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = make_file(
            ["ID заказа", "Жалоба"],
            [["ORD-555", "Повреждена упаковка"]],
        )
        result = ImportService.import_with_mapping(file, mapping)

        db_session.refresh(order)
        assert order.complaint_text == "Повреждена упаковка"
        assert result["skipped"] == 0


class TestPickerStats:
    def test_picker_stats_aggregates(self, db_session, regular_user):
        for num, count, wait, speed in [
            ("P-1", 42, "5 мин", "12/час"),
            ("P-2", 17, "8 мин", "9/час"),
        ]:
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=num,
                    picker_fio=regular_user.fio if num == "P-1" else "Другой Сборщик",
                    pick_count=count,
                    wait_time=wait,
                    pick_speed=speed,
                )
            )
        db_session.commit()

        stats = DashboardService.picker_stats()
        assert len(stats) == 2

        mine = next(s for s in stats if s["fio"] == regular_user.fio)
        assert mine["pick_count"] == 42
        assert mine["wait_time"] == "5 мин"
        assert mine["pick_speed"] == "12/час"
        assert mine["orders_count"] == 1
        assert mine["user"] is not None
        assert mine["user"].id == regular_user.id

        other = next(s for s in stats if s["fio"] == "Другой Сборщик")
        assert other["user"] is None

        # Sorted by pick_count desc
        assert stats[0]["fio"] == regular_user.fio

    def test_picker_stats_empty(self, db_session):
        assert DashboardService.picker_stats() == []


class TestComplaintCouriers:
    def test_complaint_order_shows_courier_info(self, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="ORD-9",
            external_id="9",
            courier_fio="Курьер Курьеров",
            address="ул. Победы, 10",
            delivered_at="01.10.2026 18:30",
            complaint_text="Не вручен вовремя",
        )
        db_session.add(order)
        db_session.commit()

        rows = DashboardService.complaint_couriers()
        assert len(rows) == 1
        row = rows[0]
        assert row["courier"] == "Курьер Курьеров"
        assert row["address"] == "ул. Победы, 10"
        assert row["delivered_at"] == "01.10.2026 18:30"
        assert row["external_id"] == "9"
        assert row["order_number"] == "ORD-9"
        assert row["has_photos"] is False

    def test_courier_fallback_to_user_fio(self, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="ORD-10",
            complaint_text="Жалоба",
        )
        db_session.add(order)
        db_session.commit()

        rows = DashboardService.complaint_couriers()
        assert len(rows) == 1
        assert rows[0]["courier"] == regular_user.fio

    def test_no_complaints_empty(self, db_session):
        assert DashboardService.complaint_couriers() == []


class TestPickerRoutes:
    def test_pickers_page_requires_admin(self, client):
        resp = client.get("/admin/pickers")
        assert resp.status_code == 302

    def test_pickers_page_renders(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="ORD-11",
                picker_fio=regular_user.fio,
                pick_count=30,
                wait_time="4 мин",
                pick_speed="15/час",
            )
        )
        db_session.commit()

        resp = admin_client.get("/admin/pickers")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Дашборд сборщиков" in html
        assert regular_user.fio in html
        assert "15/час" in html
        assert "Сборщики" in html

    def test_user_dashboard_shows_picker_block(self, auth_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="ORD-12",
                picker_fio=regular_user.fio,
                pick_count=42,
                wait_time="5 мин",
                pick_speed="12/час",
            )
        )
        db_session.commit()

        resp = auth_client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Мои показатели сборки" in html
        assert "Скорость сборки" in html
        assert "12/час" in html


class TestPickerRanking:
    def test_score_is_speed_plus_wait(self, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="R-1",
                picker_fio="Рейтинговый Сборщик",
                pick_count=10,
                wait_time="5 мин",
                pick_speed="12/час",
            )
        )
        db_session.commit()

        stats = DashboardService.picker_stats()
        assert len(stats) == 1
        assert stats[0]["score"] == 17.0

    def test_sorted_by_score_desc(self, db_session, regular_user):
        for num, fio, wait, speed in [
            ("R-2", "Медленный", "30 мин", "2/час"),
            ("R-3", "Быстрый", "5 мин", "30/час"),
        ]:
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=num,
                    picker_fio=fio,
                    wait_time=wait,
                    pick_speed=speed,
                )
            )
        db_session.commit()

        stats = DashboardService.picker_stats()
        assert [s["fio"] for s in stats] == ["Быстрый", "Медленный"]
        assert stats[0]["score"] == 35.0
        assert stats[1]["score"] == 32.0

    def test_no_numbers_gives_zero_score(self, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="R-4",
                picker_fio="Пустой",
                wait_time="—",
                pick_speed="—",
            )
        )
        db_session.commit()

        stats = DashboardService.picker_stats()
        assert stats[0]["score"] == 0.0


class TestPhotoRequiredTopics:
    def test_whitelisted_topic_requires_photo(self, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="PH-1",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order)
        db_session.commit()

        row = DashboardService.complaint_couriers()[0]
        assert row["photo_required"] is True

    def test_other_topic_does_not_require_photo(self, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="PH-2",
            complaint_text="Предиктив доставка",
        )
        db_session.add(order)
        db_session.commit()

        row = DashboardService.complaint_couriers()[0]
        assert row["photo_required"] is False

    def test_all_four_topics_matched(self, db_session, regular_user):
        from app.services.dashboard_service import PHOTO_REQUIRED_TOPICS

        assert PHOTO_REQUIRED_TOPICS == {
            "Товар побит/вскрыт",
            "Не донесли часть товаров из заказа",
            "Не учли комментарий к заказу",
            "Принесли чужой заказ",
        }


class TestCouriersRoute:
    def test_couriers_page_requires_admin(self, client):
        resp = client.get("/admin/couriers")
        assert resp.status_code == 302

    def test_couriers_page_renders(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PH-3",
                external_id="PH-3",
                courier_fio="Курьер Тестов",
                address="ул. Тестовая, 1",
                delivered_at="05.10.2026 12:00",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()

        resp = admin_client.get("/admin/couriers")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Дашборд курьеров" in html
        assert "Курьер Тестов" in html
        assert "ул. Тестовая, 1" in html
        assert "Запросить фото" in html

    def test_photo_button_hidden_for_other_topics(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PH-4",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PH-5",
                complaint_text="Предиктив доставка",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/couriers").get_data(as_text=True)
        assert html.count("Запросить фото") == 1


class TestDashboardRestructure:
    def test_main_screen_keeps_required_blocks(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="DS-1",
                picker_fio=regular_user.fio,
                pick_count=42,
                wait_time="5 мин",
                pick_speed="12/час",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/").get_data(as_text=True)
        # Stats cards
        assert "Курьеров" in html
        assert "Ожидают фото" in html
        assert "Общий процент выполнения" in html
        # Quick actions: only users, orders, pickers, couriers
        assert "Пользователи" in html
        assert "Все заказы" in html
        assert "Сборщики" in html
        assert "Курьеры" in html
        # Best pickers block
        assert "Лучшие сборщики" in html
        assert regular_user.fio in html
        # Storage kept
        assert "Хранилище" in html

    def test_main_screen_removes_old_blocks(self, admin_client):
        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "Всего заказов" not in html
        assert "Подтверждено" not in html
        assert "Последние фото" not in html
        assert "Импорт Excel" not in html
        assert "Маппинги" not in html
        assert "Экспорт" not in html

    def test_pickers_page_has_no_complaints_section(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="DS-2",
                picker_fio=regular_user.fio,
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/pickers").get_data(as_text=True)
        assert "заказы с жалобами" not in html
        assert "Запросить фото" not in html
