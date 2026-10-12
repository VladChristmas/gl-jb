import io
import re
from datetime import datetime

import openpyxl
import pytest

from app.models import Complaint, ImportMapping, Order, Photo, User
from app.services.dashboard_service import DashboardService
from app.services.import_service import ImportService
from app.services.rating_service import week_monday


@pytest.fixture(autouse=True)
def clean_orders(db_session):
    """Isolate from leftover orders created by other tests (session-scoped DB)."""
    Complaint.query.delete()
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
    def test_score_is_speed_minus_wait(self, db_session, regular_user):
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
        # 12/час - 5 мин = 7: выше скорость и ниже ожидание — тем лучше
        assert stats[0]["score"] == 7.0

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
        assert stats[0]["score"] == 25.0
        assert stats[1]["score"] == -28.0

    def test_higher_wait_lowers_score(self, db_session, regular_user):
        for num, wait in [("R-5", "2 мин"), ("R-6", "20 мин")]:
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=num,
                    picker_fio=f"Сборщик-{num}",
                    wait_time=wait,
                    pick_speed="10/час",
                )
            )
        db_session.commit()

        stats = DashboardService.picker_stats()
        # При одинаковой скорости меньше ожидание — лучше
        assert stats[0]["score"] > stats[1]["score"]

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

    def test_all_five_topics_matched(self, db_session, regular_user):
        from app.services.dashboard_service import PHOTO_REQUIRED_TOPICS

        assert PHOTO_REQUIRED_TOPICS == {
            "Товар побит/вскрыт",
            "Товар поврежден фото",
            "Не донесли часть товаров из заказа",
            "Не учли комментарий к заказу",
            "Принесли чужой заказ",
        }


class TestCouriersRoute:
    def test_couriers_page_requires_admin(self, client):
        resp = client.get("/admin/couriers")
        assert resp.status_code == 302

    def test_couriers_page_renders(self, admin_client, db_session, regular_user):
        monday = week_monday(None)
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PH-3",
                external_id="PH-3",
                courier_fio="Курьер Тестов",
                address="ул. Тестовая, 1",
                delivered_at=monday.strftime("%d.%m.%Y 12:00"),
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()

        resp = admin_client.get("/admin/couriers")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Этап 17: страница переведена в «Рейтинг курьеров» (KPI, недели)
        assert "Рейтинг курьеров" in html
        assert "Заказов в периоде" in html
        # Курьер с заказом текущей недели попадает в блок «Недостаточно данных»
        assert "Курьер Тестов" in html
        # Счётчик ждущих фото (жалоба с фото-подтемой без фото)
        assert "Ожидают фото" in html
        # Навигация по неделям
        assert "Сейчас" in html

    def test_photo_button_hidden_for_other_topics(self, admin_client, db_session, regular_user):
        """Фото-бейдж «ждём фото» — только для фото-подтем (жалобы, §4)."""
        order_photo = Order(
            user_id=regular_user.id, order_number="PH-4", complaint_text="Товар побит/вскрыт"
        )
        order_other = Order(
            user_id=regular_user.id, order_number="PH-5", complaint_text="Предиктив доставка"
        )
        db_session.add_all([order_photo, order_other])
        db_session.commit()
        db_session.add(Complaint(order_id=order_photo.id, text="Товар побит/вскрыт", status="new"))
        db_session.add(Complaint(order_id=order_other.id, text="Предиктив доставка", status="new"))
        db_session.commit()

        html = admin_client.get("/admin/complaints?search=PH-4").get_data(as_text=True)
        assert html.count("ждём фото") == 1

        html = admin_client.get("/admin/complaints?search=PH-5").get_data(as_text=True)
        assert "ждём фото" not in html

    def test_couriers_page_shows_rating_card(self, admin_client, db_session, regular_user):
        # Правило §4: заказ участвует в неделе только с распознанной датой доставки —
        # проставляем понедельник текущей недели; порог минимальной выборки
        # (RATING_MIN_ORDERS=10 по умолчанию) для карточки снижаем до 1.
        monday = week_monday(None)
        delivered = monday.strftime("%d.%m.%Y 12:00")
        admin_client.application.config["RATING_MIN_ORDERS"] = 1
        try:
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number="RT-1",
                    courier_fio="Курьер Рейтинг",
                    delivered_at=delivered,
                    complaint_text="Товар побит/вскрыт",
                )
            )
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number="RT-2",
                    courier_fio="Курьер Рейтинг",
                    delivered_at=delivered,
                )
            )
            db_session.commit()

            html = admin_client.get("/admin/couriers").get_data(as_text=True)
            assert "Рейтинг курьеров" in html
            assert "Заказов с жалобами, %" in html
            assert "Курьер Рейтинг" in html
            assert "50.0" in html
        finally:
            admin_client.application.config["RATING_MIN_ORDERS"] = 10


class TestBestCouriers:
    def test_fewer_complaints_ranks_higher(self, db_session, regular_user):
        for i in range(3):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-1-{i}",
                    courier_fio="Курьер с жалобами",
                    complaint_text="Товар побит/вскрыт" if i == 0 else None,
                )
            )
        for i in range(2):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-2-{i}",
                    courier_fio="Идеальный курьер",
                )
            )
        db_session.commit()

        best = DashboardService.best_couriers()
        assert [c["fio"] for c in best] == ["Идеальный курьер", "Курьер с жалобами"]
        assert best[0]["complaints_count"] == 0
        assert best[0]["orders_count"] == 2
        assert best[1]["complaints_count"] == 1
        assert best[1]["orders_count"] == 3

    def test_tie_break_by_more_orders(self, db_session, regular_user):
        for i in range(5):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-3-{i}",
                    courier_fio="Большой маршрут",
                )
            )
        for i in range(2):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-4-{i}",
                    courier_fio="Малый маршрут",
                )
            )
        db_session.commit()

        best = DashboardService.best_couriers()
        assert best[0]["fio"] == "Большой маршрут"
        assert best[1]["fio"] == "Малый маршрут"

    def test_orders_without_courier_excluded(self, db_session, regular_user):
        db_session.add(Order(user_id=regular_user.id, order_number="BC-5"))
        db_session.commit()

        assert DashboardService.best_couriers() == []

    def test_non_photo_complaint_counted_separately(self, db_session, regular_user):
        """§6: в рейтинге учитываются все жалобы; фото-подтемы — отдельная метрика
        `photo_required_count` (старое правило §13 больше не скрывает обычные жалобы)."""
        for i in range(2):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-6-{i}",
                    courier_fio="Курьер с обычными жалобами",
                    complaint_text="Предиктив доставка",
                )
            )
        for i in range(3):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-7-{i}",
                    courier_fio="Курьер без жалоб",
                )
            )
        db_session.commit()

        best = DashboardService.best_couriers()
        by_fio = {c["fio"]: c for c in best}
        # Обычные жалобы посчитаны как жалобы, но не как «нужно фото»
        assert by_fio["Курьер с обычными жалобами"]["complaints_count"] == 2
        assert by_fio["Курьер с обычными жалобами"]["orders_with_complaints_count"] == 2
        assert by_fio["Курьер с обычными жалобами"]["photo_required_count"] == 0
        assert by_fio["Курьер без жалоб"]["complaints_count"] == 0
        assert by_fio["Курьер без жалоб"]["orders_with_complaints_count"] == 0
        # Ранжирование по доле заказов с жалобами: 0% лучше 100%
        assert best[0]["fio"] == "Курьер без жалоб"

    def test_complaint_rate_per_100_delivered(self, db_session, regular_user):
        for i in range(3):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-8-{i}",
                    courier_fio="Курьер с процентом",
                )
            )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="BC-8-3",
                courier_fio="Курьер с процентом",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()

        by_fio = {c["fio"]: c for c in DashboardService.best_couriers()}
        courier = by_fio["Курьер с процентом"]
        assert courier["orders_count"] == 4
        assert courier["delivered_count"] == 4
        assert courier["complaints_count"] == 1
        assert courier["complaint_rate"] == 25.0

    def test_rate_normalizes_route_size(self, db_session, regular_user):
        """Большой маршрут с 2 жалобами лучше маленького с 1 — при rate 20.0 < 50.0."""
        for i in range(8):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-9-{i}",
                    courier_fio="Большой маршрут",
                )
            )
        for i in range(2):
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number=f"BC-10-{i}",
                    courier_fio="Большой маршрут",
                    complaint_text="Товар побит/вскрыт",
                )
            )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="BC-11-0",
                courier_fio="Малый маршрут",
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="BC-11-1",
                courier_fio="Малый маршрут",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.commit()

        best = DashboardService.best_couriers()
        assert best[0]["fio"] == "Большой маршрут"
        assert best[0]["complaints_count"] == 2
        assert best[0]["complaint_rate"] == 20.0
        assert best[1]["fio"] == "Малый маршрут"
        assert best[1]["complaints_count"] == 1
        assert best[1]["complaint_rate"] == 50.0


class TestComplaintPlaceholder:
    def test_unmatched_complaint_creates_placeholder_order(self, db_session, regular_user):
        mapping = ImportMapping(
            name="complaints-new",
            type="complaints",
            id_column="Номер заказа",
            field_mapping={"Подтема": "complaint_text"},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = make_file(
            ["Номер заказа", "Подтема"],
            [["7fe5e71f-15ba-4734-a44c-a4d0e9b7276d", "Товар побит/вскрыт"]],
        )
        result = ImportService.import_with_mapping(file, mapping)
        assert result["imported"] == 1
        assert result["skipped"] == 0

        order = Order.query.filter_by(external_id="7fe5e71f-15ba-4734-a44c-a4d0e9b7276d").first()
        assert order is not None
        assert order.complaint_text == "Товар побит/вскрыт"
        user = User.query.get(order.user_id)
        assert user is not None
        assert user.fio == "Не указан"

        # Повторный импорт — заказ находится, дубль не создаётся
        file = make_file(
            ["Номер заказа", "Подтема"],
            [["7fe5e71f-15ba-4734-a44c-a4d0e9b7276d", "Товар побит/вскрыт"]],
        )
        result = ImportService.import_with_mapping(file, mapping)
        assert result["imported"] == 0
        assert result["updated"] == 1
        assert (
            Order.query.filter_by(external_id="7fe5e71f-15ba-4734-a44c-a4d0e9b7276d").count() == 1
        )

    def test_non_photo_topic_skipped_without_placeholder(self, db_session, regular_user):
        mapping = ImportMapping(
            name="complaints-skip",
            type="complaints",
            id_column="Номер заказа",
            field_mapping={"Подтема": "complaint_text"},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = make_file(
            ["Номер заказа", "Подтема"],
            [["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "Предиктив доставка"]],
        )
        result = ImportService.import_with_mapping(file, mapping)
        # Заказа нет, подтема не требует фото — пропускаем без заглушки
        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert (
            Order.query.filter_by(external_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee").count() == 0
        )

    def test_second_topic_appended_not_overwritten(self, db_session, regular_user):
        mapping = ImportMapping(
            name="complaints-append",
            type="complaints",
            id_column="Номер заказа",
            field_mapping={"Подтема": "complaint_text"},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = make_file(
            ["Номер заказа", "Подтема"],
            [
                ["APP-1", "Не донесли часть товаров из заказа"],
                ["APP-1", "Старт"],
            ],
        )
        ImportService.import_with_mapping(file, mapping)

        # Жалобы без заказа → обе строки создают заглушки (APP-1 = external_id)
        order = Order.query.filter_by(external_id="APP-1").first()
        assert order is not None
        assert order.complaint_text == "Не донесли часть товаров из заказа; Старт"

        # Whitelist-подтема найдена как подстрока → кнопка фото остаётся
        rows = [r for r in DashboardService.complaint_couriers() if r["external_id"] == "APP-1"]
        assert rows and rows[0]["photo_required"] is True


class TestDashboardRestructure:
    def test_main_screen_keeps_required_blocks(self, admin_client, db_session, regular_user):
        # Правило §4 недельного рейтинга: без даты доставки заказ не входит в
        # период, поэтому проставляем понедельник текущей недели; минимальную
        # выборку для карточки снижаем до 1.
        delivered = week_monday(None).strftime("%d.%m.%Y 12:00")
        admin_client.application.config["RATING_MIN_ORDERS"] = 1
        try:
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number="DS-1",
                    picker_fio=regular_user.fio,
                    pick_count=42,
                    wait_time="5 мин",
                    pick_speed="12/час",
                    courier_fio="Курьер Тестов",
                    delivered_at=delivered,
                    complaint_text="Предиктив доставка",
                )
            )
            db_session.commit()

            html = admin_client.get("/admin/").get_data(as_text=True)
            # Stats cards
            assert "Рейтинг курьеров" in html
            assert "Ожидают фото" in html
            assert "Общий процент выполнения" in html
            # KPI grid
            assert "kpi-grid" in html
            # Quick actions: users, orders, pickers, couriers, chat
            assert "Пользователи" in html
            assert "Все заказы" in html
            assert "Сборщики" in html
            assert "Курьеры" in html
            assert "Чат" in html
            # Best pickers and best couriers
            assert "Лучшие сборщики" in html
            assert "Лучшие курьеры" in html
            assert regular_user.fio in html
            assert "Курьер Тестов" in html
            assert "заказов с жалобами из" in html
            # Storage kept
            assert "Хранилище" in html
        finally:
            admin_client.application.config["RATING_MIN_ORDERS"] = 10

    def test_main_screen_removes_old_blocks(self, admin_client):
        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "Всего заказов" not in html
        assert "Подтверждено" not in html
        assert "Последние фото" not in html
        assert "Последние заказы" not in html
        assert "Ждут подтверждения" not in html
        assert "Импорт Excel" not in html
        assert "Маппинги" not in html
        assert "Экспорт" not in html

    def test_pending_block_shows_count_and_links(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PEND-1",
                courier_fio="Курьер Ожидаемый",
                address="ул. Фото, 1",
                delivered_at="05.10.2026 12:00",
                complaint_text="Товар побит/вскрыт",
                created_at=datetime.utcnow(),
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PEND-2",
                complaint_text="Не учли комментарий к заказу",
                created_at=datetime.utcnow(),
            )
        )
        # Заказ без фото-подтемы — в блок НЕ попадает
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PEND-X",
                complaint_text="Предиктив доставка",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "Ожидают фото" in html
        assert "id='pending-count'" in html
        # Оба заказа с фото-подтемой показаны, строки содержат ФИО/адрес/время
        assert "PEND-1" in html
        assert "PEND-2" in html
        assert "Курьер Ожидаемый" in html
        assert "ул. Фото, 1" in html
        assert "05.10.2026 12:00" in html
        # Заказ без фото-подтемы не показывается
        assert "PEND-X" not in html
        # Счётчик в бейдже >= 2 (in-memory session-scoped DB may have leftovers from earlier tests)
        m = re.search(r"id='pending-count'[^>]*>\s*(\d+)", html)
        assert m and int(m.group(1)) >= 2
        # Ссылка на список заказов без фото
        assert "photo_filter=without_photos" in html
        # Клик ведёт на страницу заказа
        assert "href='/admin/orders/edit/" in html

    def test_pending_count_api(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PEND-3",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="PEND-4",
                complaint_text="Предиктив доставка",
            )
        )
        db_session.commit()

        # С админ-сессией — 200 и живой счётчик (только фото-подтемы)
        resp = admin_client.get("/admin/api/pending_count")
        assert resp.status_code == 200
        assert resp.get_json()["pending"] == 1

        # После логаута — 403
        admin_client.get("/admin/logout")
        resp = admin_client.get("/admin/api/pending_count")
        assert resp.status_code == 403

    def test_block_order(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="DS-3",
                picker_fio=regular_user.fio,
                pick_count=1,
                wait_time="1 мин",
                pick_speed="1/час",
                courier_fio="Курьер Тестов",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/").get_data(as_text=True)
        idx_pickers = html.index("Лучшие сборщики")
        idx_couriers = html.index("Лучшие курьеры")
        idx_pending = html.index("id='pending-count'")
        # Этап 5 (проблемы впереди): «Ожидают фото» — выше рейтингов
        assert idx_pending < idx_pickers
        assert idx_pending < idx_couriers
        # Рейтинги идут подряд (между ними нет других секций)
        assert idx_couriers - idx_pickers < 6000

    def test_attention_block_links_carry_filters(self, admin_client, db_session, regular_user):
        """§4: каждая метрика-проблема открывает список с тем же фильтром."""
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="ATT-1",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="ATT-2",
                complaint_text="Не учли комментарий к заказу",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "Требует внимания" in html
        # Ожидают фото (ATT-1: фото-подтема без фото) → фильтр without_photos
        assert "photo_filter=without_photos" in html
        # Заказы с жалобами (оба) → фильтр with_complaints
        assert "complaint_filter=with_complaints" in html
        # KPI «С фото» → with_photos; «Заказов» → полный список
        assert "photo_filter=with_photos" in html

        # Достоверность: счётчик жалоб = уникальные заказы, а не штук жалоб.
        # Обе подтемы фото-обязательные → pending_count == 2.
        stats = DashboardService.admin_stats()
        assert stats["orders_with_complaints"] == 2
        assert stats["pending_count"] >= 2  # session-scoped DB may have leftovers

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

    def test_couriers_table_keeps_actions_on_mobile(self, admin_client, db_session, regular_user):
        """Мобильный вид: таблица заменяется карточками со ссылкой на курьера."""
        admin_client.application.config["RATING_MIN_ORDERS"] = 1
        try:
            monday = week_monday(None)
            db_session.add(
                Order(
                    user_id=regular_user.id,
                    order_number="MB-1",
                    courier_fio="Курьер Мобильный",
                    delivered_at=monday.strftime("%d.%m.%Y 12:00"),
                    complaint_text="Товар побит/вскрыт",
                )
            )
            db_session.commit()

            html = admin_client.get("/admin/couriers").get_data(as_text=True)
            assert 'id="desktop-table"' in html
            assert 'id="mobile-cards"' in html
            assert "courier-card" in html
            # карточка ведёт на детальную страницу курьера
            assert "/admin/courier/" in html
        finally:
            admin_client.application.config["RATING_MIN_ORDERS"] = 10


class TestCouriersAwaitingCount:
    STAT_RE = (
        r'<div class="stat-number"[^>]*>\s*(\d+)\s*</div>\s*'
        r'<div class="stat-label">Ожидают фото</div>'
    )

    def test_counts_only_photo_topics(self, admin_client, db_session, regular_user):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="AW-1",
                courier_fio="Курьер Фото",
                complaint_text="Товар побит/вскрыт",
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="AW-2",
                courier_fio="Курьер Обычный",
                complaint_text="Предиктив доставка",
            )
        )
        db_session.commit()

        html = admin_client.get("/admin/couriers").get_data(as_text=True)
        m = re.search(self.STAT_RE, html)
        assert m and int(m.group(1)) == 1

    def test_confirmed_photo_not_counted(self, admin_client, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="AW-3",
            courier_fio="Курьер Подтвердил",
            complaint_text="Не донесли часть товаров из заказа",
        )
        db_session.add(order)
        db_session.commit()
        db_session.add(Photo(order_id=order.id, filename="a.jpg", original_filename="a.jpg"))
        db_session.commit()

        html = admin_client.get("/admin/couriers").get_data(as_text=True)
        m = re.search(self.STAT_RE, html)
        assert m and int(m.group(1)) == 0


class TestOrdersHistoryFilter:
    def test_orders_page_hides_orders_without_photo_and_topic(
        self, admin_client, db_session, regular_user
    ):
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="ORD-WITH",
                complaint_text="Принесли чужой заказ",
            )
        )
        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="ORD-SKIP",
                complaint_text="Предиктив доставка",
            )
        )
        db_session.add(Order(user_id=regular_user.id, order_number="ORD-PLAIN"))
        db_session.commit()

        # §4: по умолчанию список показывает все заказы (как KPI дашборда)
        html = admin_client.get("/admin/orders").get_data(as_text=True)
        assert "ORD-WITH" in html
        assert "ORD-SKIP" in html
        assert "ORD-PLAIN" in html

        # Старое правило «с фото и ждущие фото» доступно явным фильтром
        html = admin_client.get("/admin/orders?photo_filter=photo_relevant").get_data(as_text=True)
        assert "ORD-WITH" in html
        assert "ORD-SKIP" not in html
        assert "ORD-PLAIN" not in html

    def test_orders_page_shows_confirmed_photo_order(self, admin_client, db_session, regular_user):
        order = Order(user_id=regular_user.id, order_number="ORD-PHOTO")
        db_session.add(order)
        db_session.commit()
        db_session.add(Photo(order_id=order.id, filename="b.jpg", original_filename="b.jpg"))
        db_session.commit()

        html = admin_client.get("/admin/orders").get_data(as_text=True)
        assert "ORD-PHOTO" in html


class TestOrderDescription:
    def test_edit_order_shows_details(self, admin_client, db_session, regular_user):
        order = Order(
            user_id=regular_user.id,
            order_number="D-777",
            external_id="uuid-d777",
            courier_fio="Курьер Описания",
            address="ул. Ленина, 5",
            delivered_at="05.10.2026 12:00",
            complaint_text="Товар побит/вскрыт",
        )
        db_session.add(order)
        db_session.commit()

        html = admin_client.get(f"/admin/orders/edit/{order.id}").get_data(as_text=True)
        assert "D-777" in html
        assert "uuid-d777" in html
        assert "Товар побит/вскрыт" in html
        assert "Курьер Описания" in html
        assert "ул. Ленина, 5" in html
        assert "05.10.2026 12:00" in html
