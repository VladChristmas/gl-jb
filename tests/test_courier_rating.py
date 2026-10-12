"""Регресс-тесты еженедельного рейтинга курьеров (§13 брифа).

Изоляция: каждый тест работает в своей календарной неделе 2027 года (в данных
нет заказов с такими датами) и создаёт заказы с префиксом номера «NR» —
autouse-фикстура удаляет только их, session-БД остаётся общей.
"""

import uuid
from datetime import date, datetime

import pytest

from app.models import Complaint, Order, User
from app.services.rating_service import (
    RatingService,
    parse_delivered_at,
    week_bounds,
    week_monday,
)

# Понедельники недель, изолированных от остальных тестов (2027 год)
W1 = date(2027, 1, 4)  # Пн 04.01.2027 — Вс 10.01.2027
W0 = date(2026, 12, 28)  # предыдущая неделя
W2 = date(2027, 1, 11)  # следующая неделя
W3 = date(2027, 1, 18)

_COUNTER = {"n": 0}


def _marker() -> str:
    _COUNTER["n"] += 1
    return f"NR{_COUNTER['n']:03d}-{uuid.uuid4().hex[:6]}"


@pytest.fixture(autouse=True)
def clean_rating_data(db_session):
    """Удаляет заказы/жалобы/пользователей, созданные этим файлом."""
    yield
    numbers = [f"{order.order_number}" for order in Order.query.all()]
    mine = [n for n in numbers if n and n.startswith("NR")]
    if mine:
        ids = [
            order.id
            for order in Order.query.all()
            if order.order_number and order.order_number.startswith("NR")
        ]
        Complaint.query.filter(Complaint.order_id.in_(ids)).delete(synchronize_session=False)
        Order.query.filter(Order.id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.fio.like("NRK-%")).delete(synchronize_session=False)
    db_session.commit()


@pytest.fixture
def set_min_orders(app):
    original = app.config["RATING_MIN_ORDERS"]

    def _set(value: int) -> int:
        app.config["RATING_MIN_ORDERS"] = value
        return value

    yield _set
    app.config["RATING_MIN_ORDERS"] = original


@pytest.fixture
def set_penalty(app):
    original = app.config["RATING_PENALTY_PER_PERCENT"]

    def _set(value: float) -> float:
        app.config["RATING_PENALTY_PER_PERCENT"] = value
        return value

    yield _set
    app.config["RATING_PENALTY_PER_PERCENT"] = original


def make_order(
    db_session,
    user,
    fio: str,
    delivered: str | None = None,
    complaint: str | None = None,
    **kwargs,
) -> Order:
    order = Order(
        user_id=user.id,
        order_number=kwargs.pop("number", f"NR-{uuid.uuid4().hex[:10]}"),
        courier_fio=fio,
        delivered_at=delivered,
        complaint_text=complaint,
        **kwargs,
    )
    db_session.add(order)
    db_session.commit()
    return order


def row_by_fio(report, fio: str) -> dict | None:
    for row in report["rows"] + report["insufficient"]:
        if row["fio"] == fio:
            return row
    return None


def delivered(monday: date, day_offset: int = 0, hour: int = 12) -> str:
    return (monday.fromordinal(monday.toordinal() + day_offset)).strftime(f"%d.%m.%Y {hour:02d}:00")


class TestWeekMath:
    def test_week_bounds_are_half_open_monday_to_monday(self):
        start, end = week_bounds(W1)
        assert start == datetime(2027, 1, 4, 0, 0)
        assert end == datetime(2027, 1, 11, 0, 0)
        # Воскресенье 23:59 внутри недели, понедельник 00:00 — уже следующая
        assert start <= datetime(2027, 1, 10, 23, 59) < end
        assert not (start <= datetime(2027, 1, 11, 0, 0) < end)

    def test_week_param_normalizes_any_date_to_monday(self):
        assert week_monday("2027-01-07") == W1
        assert week_monday("10.01.2027") == W1
        assert week_monday("2027-01-04") == W1

    def test_default_week_is_current_utc_monday(self):
        expected = datetime.utcnow().date()
        expected = expected.fromordinal(expected.toordinal() - expected.weekday())
        assert week_monday(None) == expected
        assert week_monday("not-a-date") == expected

    def test_sunday_counts_next_monday_does_not(self, db_session, regular_user, set_min_orders):
        set_min_orders(1)
        fio = _marker()
        make_order(db_session, regular_user, fio, delivered=delivered(W1, 6, 23))  # Вс
        report = RatingService.week_report(W1.isoformat())
        assert row_by_fio(report, fio)["eligible_orders"] == 1

        make_order(db_session, regular_user, fio, delivered=delivered(W1, 7, 0))  # Пн 00:00
        report_next = RatingService.week_report(W2.isoformat())
        assert row_by_fio(report_next, fio)["eligible_orders"] == 1
        report_back = RatingService.week_report(W1.isoformat())
        assert row_by_fio(report_back, fio)["eligible_orders"] == 1

    def test_parse_delivered_at_formats_and_failures(self):
        assert parse_delivered_at("04.01.2027 12:30") == datetime(2027, 1, 4, 12, 30)
        assert parse_delivered_at("04.01.2027") == datetime(2027, 1, 4)
        assert parse_delivered_at("2027-01-04 12:00:00") == datetime(2027, 1, 4, 12, 0)
        assert parse_delivered_at("вчера") is None
        assert parse_delivered_at("") is None
        assert parse_delivered_at(None) is None


class TestDateAttribution:
    def test_order_without_delivery_date_is_excluded_and_counted(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(1)
        fio = _marker()
        make_order(db_session, regular_user, fio, delivered=delivered(W1))
        make_order(db_session, regular_user, fio, delivered=None)
        make_order(db_session, regular_user, fio, delivered="непонятно")

        row = row_by_fio(RatingService.week_report(W1.isoformat()), fio)
        assert row["eligible_orders"] == 1
        assert row["orders_without_date"] == 2
        assert row["total_orders"] == 3

    def test_rate_uses_period_orders_not_all_time(self, db_session, regular_user, set_min_orders):
        set_min_orders(1)
        fio = _marker()
        make_order(db_session, regular_user, fio, delivered=delivered(W1))  # в периоде, без жалобы
        # вне периода — с жалобами: на неделю влиять не должны
        make_order(db_session, regular_user, fio, delivered=delivered(W2), complaint="Ж1")
        make_order(db_session, regular_user, fio, delivered=delivered(W2), complaint="Ж2")

        row = row_by_fio(RatingService.week_report(W1.isoformat()), fio)
        assert row["eligible_orders"] == 1
        assert row["orders_with_complaints"] == 0
        assert row["complaint_rate"] == 0.0
        assert row["quality_score"] == 100.0

    def test_complaint_counts_follow_order_week_not_complaint_date(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(1)
        fio = _marker()
        # Жалoba зарегистрирована в июне 2026, заказ доставлен в неделю W1
        order = make_order(db_session, regular_user, fio, delivered=delivered(W1))
        db_session.add(
            Complaint(order_id=order.id, text="Опоздание", created_at=datetime(2026, 6, 1))
        )
        # Заказ вне периода, но жалоба зарегистрирована внутри W1
        outside = make_order(db_session, regular_user, fio, delivered=delivered(W2))
        db_session.add(
            Complaint(order_id=outside.id, text="Повреждение", created_at=datetime(2027, 1, 5))
        )
        db_session.commit()

        report = RatingService.week_report(W1.isoformat())
        row = row_by_fio(report, fio)
        assert row["complaints_count"] == 1
        assert row["orders_with_complaints"] == 1
        assert row["complaint_rate"] == 100.0
        # KPI «по дате обращения» — отдельный показатель, не смешивается
        assert report["kpi"]["complaints_registered"] == 1
        assert report["kpi"]["complaints_count"] == 1

    def test_mirror_complaint_parts_count_as_complaints(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(1)
        fio = _marker()
        make_order(
            db_session,
            regular_user,
            fio,
            delivered=delivered(W1),
            complaint="Товар побит/вскрыт; Не учли комментарий к заказу",
        )
        row = row_by_fio(RatingService.week_report(W1.isoformat()), fio)
        assert row["orders_with_complaints"] == 1
        assert row["complaints_count"] == 2


class TestScoreAndStatuses:
    def test_score_formula_range_and_clamping(
        self, db_session, regular_user, set_min_orders, set_penalty
    ):
        set_min_orders(2)
        set_penalty(1.0)
        clean = _marker()
        half = _marker()
        full = _marker()
        for _ in range(2):
            make_order(db_session, regular_user, clean, delivered=delivered(W1))
        make_order(db_session, regular_user, half, delivered=delivered(W1))
        make_order(db_session, regular_user, half, delivered=delivered(W1), complaint="Ж")
        for _ in range(2):
            make_order(db_session, regular_user, full, delivered=delivered(W1), complaint="Ж")
        report = RatingService.week_report(W1.isoformat())
        assert row_by_fio(report, clean)["quality_score"] == 100.0
        assert row_by_fio(report, half)["quality_score"] == 50.0
        # 100 - 100 = 0 → нижняя граница диапазона 1..100
        assert row_by_fio(report, full)["quality_score"] == 1.0

        set_penalty(2.0)
        report_k2 = RatingService.week_report(W1.isoformat())
        # rate 50% × K 2 = 100 → 0 → clamp до 1
        assert row_by_fio(report_k2, half)["quality_score"] == 1.0
        # rate 0% → 100 независимо от K
        assert row_by_fio(report_k2, clean)["quality_score"] == 100.0

    def test_score_is_not_pre_rounded_in_service(self, db_session, regular_user, set_min_orders):
        set_min_orders(3)
        fio = _marker()
        for _ in range(2):
            make_order(db_session, regular_user, fio, delivered=delivered(W1))
        make_order(db_session, regular_user, fio, delivered=delivered(W1), complaint="Ж")
        row = row_by_fio(RatingService.week_report(W1.isoformat()), fio)
        # 1 жалоба из 3 заказов → 33.333...% → балл 66.666... (сырое float-значение)
        assert row["eligible_orders"] == 3
        assert row["complaint_rate"] == pytest.approx(33.3333, abs=0.001)
        assert row["quality_score"] == pytest.approx(66.6667, abs=0.001)
        assert row["quality_score"] != round(row["quality_score"], 1)

    def test_rate_is_none_when_no_orders_in_period(self, db_session, set_min_orders, app):
        set_min_orders(10)
        fio = f"NRK-{uuid.uuid4().hex[:6]}"
        db_session.add(User(fio=fio, token=f"TOK{uuid.uuid4().hex[:8]}", role="courier"))
        db_session.commit()

        report = RatingService.week_report(W1.isoformat())
        row = row_by_fio(report, fio)
        # Курьер без заказов не исчезает молча (§6): строка есть, балла нет
        assert row is not None
        assert row["status"] == "insufficient"
        assert row["complaint_rate"] is None
        assert row["quality_score"] is None
        assert row in report["insufficient"]

    def test_insufficient_below_min_orders_goes_to_separate_block(
        self, db_session, regular_user, set_min_orders, admin_client
    ):
        set_min_orders(3)
        fio = _marker()
        for _ in range(2):
            make_order(db_session, regular_user, fio, delivered=delivered(W1))

        report = RatingService.week_report(W1.isoformat())
        assert report["rows"] == []
        assert row_by_fio(report, fio)["status"] == "insufficient"

        html = admin_client.get(f"/admin/couriers?week={W1.isoformat()}").get_data(as_text=True)
        assert "Недостаточно данных" in html
        assert fio in html

    def test_current_week_is_preliminary_past_week_is_ok(
        self, db_session, regular_user, set_min_orders, admin_client
    ):
        set_min_orders(2)
        past = _marker()
        for _ in range(2):
            make_order(db_session, regular_user, past, delivered=delivered(W1))
        report = RatingService.week_report(W1.isoformat())
        assert row_by_fio(report, past)["status"] == "ok"

        current_monday = week_monday(None)
        now_marker = _marker()
        for _ in range(2):
            make_order(db_session, regular_user, now_marker, delivered=delivered(current_monday))
        current = RatingService.week_report()
        assert row_by_fio(current, now_marker)["status"] == "preliminary"

        html = admin_client.get(f"/admin/couriers?week={current_monday.isoformat()}").get_data(
            as_text=True
        )
        assert "Предварительный рейтинг" in html


class TestRanking:
    def test_places_competition_ranking_for_equal_metrics(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(4)
        fio_a, fio_b, fio_c = _marker(), _marker(), _marker()
        for fio in (fio_a, fio_b):
            for _ in range(4):
                make_order(db_session, regular_user, fio, delivered=delivered(W1))
            make_order(db_session, regular_user, fio, delivered=delivered(W1), complaint="Ж")
        for _ in range(4):
            make_order(db_session, regular_user, fio_c, delivered=delivered(W1))

        report = RatingService.week_report(W1.isoformat())
        places = {row["fio"]: row["place"] for row in report["rows"]}
        assert places[fio_c] == 1
        # Полностью равные показатели → одно место, без искусственного преимущества
        assert places[fio_a] == 2
        assert places[fio_b] == 2
        # И следующее место — 4 (competition ranking), не 3
        assert max(places.values()) in (2, 4)

    def test_sort_and_search_parameters(self, db_session, regular_user, set_min_orders):
        set_min_orders(2)
        # Префиксы A/Z фиксируют алфавитный порядок независимо от uuid
        fio_early = f"NRK-A{uuid.uuid4().hex[:4]}Анна"
        fio_late = f"NRK-Z{uuid.uuid4().hex[:4]}Юлия"
        for fio in (fio_early, fio_late):
            for _ in range(2):
                make_order(db_session, regular_user, fio, delivered=delivered(W1))

        default = RatingService.week_report(W1.isoformat(), sort="score", sort_dir="desc")
        assert {row["fio"] for row in default["rows"]} >= {fio_early, fio_late}

        by_fio_asc = RatingService.week_report(W1.isoformat(), sort="fio", sort_dir="asc")
        names_asc = [row["fio"] for row in by_fio_asc["rows"]]
        assert names_asc.index(fio_early) < names_asc.index(fio_late)

        by_fio_desc = RatingService.week_report(W1.isoformat(), sort="fio", sort_dir="desc")
        names_desc = [row["fio"] for row in by_fio_desc["rows"]]
        assert names_desc.index(fio_late) < names_desc.index(fio_early)

        searched = RatingService.week_report(W1.isoformat(), search=fio_late)
        assert [row["fio"] for row in searched["rows"]] == [fio_late]
        assert row_by_fio(searched, fio_early) is None


class TestDynamics:
    def test_improvement_worsening_and_new_courier_directions(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(4)
        better, worse, fresh = _marker(), _marker(), _marker()

        # Прошлая неделя W0: better — 5 заказов 1 жалоба (20% → балл 80),
        # worse — 6 заказов 2 жалобы (33.3% → балл 66.67)
        for _ in range(4):
            make_order(db_session, regular_user, better, delivered=delivered(W0))
            make_order(db_session, regular_user, worse, delivered=delivered(W0))
        make_order(db_session, regular_user, better, delivered=delivered(W0), complaint="Ж")
        for _ in range(2):
            make_order(db_session, regular_user, worse, delivered=delivered(W0), complaint="Ж")

        # Текущая W1: better — 0% (балл 100, улучшение), worse — 50% (балл 50,
        # ухудшение), fresh — только в эту неделю (нет сравнения)
        for _ in range(4):
            make_order(db_session, regular_user, better, delivered=delivered(W1))
            make_order(db_session, regular_user, fresh, delivered=delivered(W1))
        for i in range(4):
            make_order(
                db_session,
                regular_user,
                worse,
                delivered=delivered(W1),
                complaint="Ж" if i < 2 else None,
            )

        report = RatingService.week_report(W1.isoformat())
        better_row = row_by_fio(report, better)
        worse_row = row_by_fio(report, worse)
        fresh_row = row_by_fio(report, fresh)

        assert better_row["direction"] == "Улучшение"
        assert better_row["deltas"]["score"] == pytest.approx(20.0)  # 80 → 100
        assert worse_row["direction"] == "Ухудшение"
        assert worse_row["deltas"]["score"] == pytest.approx(-16.67, abs=0.01)  # 66.67 → 50
        assert fresh_row["direction"] == "Недостаточно данных для сравнения"
        assert fresh_row["prev"] is None

    def test_direction_unknown_when_previous_week_insufficient(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(4)
        fio = _marker()
        # В прошлой неделе — 1 заказ (меньше выборки), в текущей — 4
        make_order(db_session, regular_user, fio, delivered=delivered(W0))
        for _ in range(4):
            make_order(db_session, regular_user, fio, delivered=delivered(W1))

        row = row_by_fio(RatingService.week_report(W1.isoformat()), fio)
        assert row["quality_score"] is not None
        assert row["direction"] == "Недостаточно данных для сравнения"

    def test_previous_week_is_computed_with_same_function(
        self, db_session, regular_user, set_min_orders
    ):
        set_min_orders(2)
        fio = _marker()
        for _ in range(3):
            make_order(db_session, regular_user, fio, delivered=delivered(W0))
        for _ in range(5):
            make_order(db_session, regular_user, fio, delivered=delivered(W1))

        row = row_by_fio(RatingService.week_report(W1.isoformat()), fio)
        assert row["prev"]["eligible_orders"] == 3
        assert row["deltas"]["orders"] == 2


class TestRoutesAndAccess:
    def test_rating_requires_admin_session(self, client):
        assert client.get("/admin/couriers").status_code == 302
        assert client.get("/admin/courier/SomeCourier").status_code == 302

    def test_rating_forbidden_for_non_admin(self, auth_client):
        assert auth_client.get("/admin/couriers").status_code == 302
        assert auth_client.get("/admin/courier/SomeCourier").status_code == 302

    def test_rating_available_for_admin(self, admin_client):
        assert admin_client.get("/admin/couriers").status_code == 200
        assert admin_client.get("/admin/courier/Unknown-Courier").status_code == 200

    def test_week_is_persisted_in_navigation_links(self, admin_client):
        html = admin_client.get(f"/admin/couriers?week={W1.isoformat()}").get_data(as_text=True)
        assert "04.01 — 10.01.2027" in html
        assert "week=2026-12-28" in html  # предыдущая
        assert "week=2027-01-11" in html  # следующая

    def test_db_error_is_shown_as_error_not_zero_rating(self, admin_client, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.services.rating_service.RatingService.week_report", boom)
        html = admin_client.get("/admin/couriers").get_data(as_text=True)
        assert "Не удалось рассчитать рейтинг" in html
        assert "Заказов в периоде" not in html
        assert "Балл" not in html

    def test_courier_detail_page_shows_week_orders(
        self, db_session, regular_user, set_min_orders, admin_client
    ):
        set_min_orders(2)
        fio = _marker()
        first = make_order(db_session, regular_user, fio, delivered=delivered(W1))
        make_order(db_session, regular_user, fio, delivered=delivered(W1), complaint="Ж")

        html = admin_client.get(f"/admin/courier/{fio}?week={W1.isoformat()}").get_data(
            as_text=True
        )
        assert "Балл" in html
        assert first.order_number in html
        assert "К прошлой неделе" in html

    def test_unknown_courier_detail_shows_not_found(self, admin_client):
        html = admin_client.get("/admin/courier/Nobody-XYZ").get_data(as_text=True)
        assert "Курьер не найден" in html

    def test_dashboard_uses_weekly_rating_source(
        self, db_session, regular_user, set_min_orders, admin_client
    ):
        set_min_orders(1)
        fio = _marker()
        current_monday = week_monday(None)
        for _ in range(6):
            make_order(db_session, regular_user, fio, delivered=delivered(current_monday))

        html = admin_client.get("/admin/").get_data(as_text=True)
        assert "Лучшие курьеры" in html
        assert fio in html


class TestKpi:
    def test_kpi_summarizes_only_the_selected_week(self, db_session, regular_user, set_min_orders):
        set_min_orders(1)
        # Недатированные заказы других курьеров (сессионная БД) не привязаны к
        # неделе и всегда попадают в KPI — фиксируем базовый уровень.
        baseline_without_date = RatingService.week_report(W1.isoformat())["kpi"][
            "orders_without_date"
        ]
        fio = _marker()
        for i in range(3):
            make_order(
                db_session,
                regular_user,
                fio,
                delivered=delivered(W1, i),
                complaint="Ж" if i == 0 else None,
            )
        make_order(db_session, regular_user, fio, delivered=delivered(W2), complaint="Ж")

        report = RatingService.week_report(W1.isoformat())
        kpi = report["kpi"]
        assert kpi["orders_total"] == 3
        assert kpi["orders_with_complaints"] == 1
        assert kpi["complaint_rate"] == pytest.approx(33.3)
        assert kpi["orders_without_date"] == baseline_without_date

    def test_kpi_rate_is_none_dash_without_orders(self, db_session, set_min_orders, admin_client):
        set_min_orders(1)
        html = admin_client.get(f"/admin/couriers?week={W3.isoformat()}").get_data(as_text=True)
        # Нет заказов за период → «—», а не 0%
        assert "—" in html
        assert "0.0% заказов" not in html
