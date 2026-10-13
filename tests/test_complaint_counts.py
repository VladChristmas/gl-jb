"""Регрессии подсчёта и фильтрации жалоб (аудит 2026-10-13).

Цель: список жалоб, KPI, аналитика, карточки курьеров и недельный отчёт
должны показывать согласованные числа.

Исправленные дефекты:

- **D-1** `AnalyticsService.courier_complaint_stats`: `func.count(Order.id)` на
  `outerjoin(Complaint)` размножал строки заказов по числу жалоб. Курьер с
  2 заказами и 4 жалобами получал `orders_count == 4`, а «Доля, %» считалась
  от размноженного знаменателя. Теперь `count(distinct(Order.id))`.
- **D-2** тот же метод фильтровал период по `Order.created_at`, тогда как
  список, тренды, причины и разбивка по статусам — по `Complaint.created_at`
  (дата обращения). Жалоба, зарегистрированная через дни после заказа,
  попадала в список, но не в таблицу курьеров. Теперь единое поле.
- **D-3** `AnalyticsService.status_breakdown` не принимал `status`, хотя
  `/admin/analytics` показывает фильтр по статусу и `complaint_trends` /
  `courier_complaint_stats` его учитывают: на одном экране блоки с разными
  числами. Добавлен параметр + проброс из route.

Правила, проверяемые тестами (нумерация из задания):
#1 одна запись жалобы учитывается один раз;
#2 JOIN не размножает записи;
#3 «жалобы» ≠ «заказы с жалобами» (разные показатели);
#4 несколько жалоб на один заказ считаются по фактической структуре;
#5 сумма взаимоисключающих статусов == общему количеству;
#6 период применяется одинаково во всех представлениях;
#7 границы периода: записи на стыке дней не теряются;
#8 закрытые жалобы не исключаются без явного фильтра.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.complaint import Complaint
from app.models.order import Order
from app.models.user import User
from app.services.complaint_service import ComplaintService
from app.services.dashboard_service import (
    AnalyticsService,
    DashboardService,
    WeeklyReportService,
)

D0 = datetime(2026, 10, 5, 12, 0)
D1 = datetime(2026, 10, 6, 12, 0)
D2 = datetime(2026, 10, 7, 12, 0)


def _marker() -> str:
    return f"AC{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def clean_complaint_audit(db_session):
    """Тестовая БД сессионная и общая: удаляем ровно созданное здесь.

    Иначе переполнение счётчиков ломает соседние тесты (`total == N`) —
    тот же приём, что в test_courier_rating.py.
    """
    order_ids: list[int] = []
    complaint_ids: list[int] = []

    def add_order(user, number, courier=None, created_at=None):
        order = Order(
            user_id=user.id,
            order_number=number,
            address=f"Адрес {number}",
            courier_fio=courier,
            created_at=created_at,
        )
        db_session.add(order)
        db_session.flush()
        order_ids.append(order.id)
        return order

    def add_complaint(order, text, status="открыта", created_at=None, order_id=None):
        complaint = Complaint(
            order_id=order_id if order_id is not None else order.id,
            text=text,
            status=status,
            created_at=created_at or D0,
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


from types import SimpleNamespace  # noqa: E402  (используется в фикстуре выше)


class TestOneComplaintOneOrder:
    """Правило #1: одна жалоба на один заказ — один раз во всех счётчиках."""

    def test_single_row_counted_once(self, db_session, regular_user, clean_complaint_audit):
        fio = f"Курьер-{_marker()}"
        order = clean_complaint_audit.order(regular_user, f"S-{_marker()}", fio, D0)
        clean_complaint_audit.complaint(order, "Опоздание")

        rows = AnalyticsService.courier_complaint_stats()
        mine = [r for r in rows if r["courier"] == fio]
        assert len(mine) == 1
        assert mine[0]["complaints_count"] == 1
        assert mine[0]["orders_count"] == 1

        page, rows = ComplaintService.list_complaints(per_page=100, search=order.order_number)
        assert page.total == 1
        assert len(rows) == 1


class TestSeveralComplaintsOneOrder:
    """Правило #4: 3 жалобы на один заказ = 3 жалобы, но 1 заказ."""

    def test_three_complaints_three_counts(self, db_session, regular_user, clean_complaint_audit):
        fio = f"Курьер-{_marker()}"
        order = clean_complaint_audit.order(regular_user, f"M-{_marker()}", fio, D0)
        for i in range(3):
            clean_complaint_audit.complaint(order, f"Жалоба {order.order_number} {i}")
        db_session.commit()

        # список: 3 строки, не 1
        page, _ = ComplaintService.list_complaints(per_page=100, search=order.order_number)
        assert page.total == 3

        # аналитика: жалобы=3, заказы=1 (правила #1/#3/#4)
        cs = {r["courier"]: r for r in AnalyticsService.courier_complaint_stats()}
        assert cs[fio]["complaints_count"] == 3
        assert cs[fio]["orders_count"] == 1

        # reasons: жалобы=3, заказов с жалобами=1
        reasons = AnalyticsService.complaint_reasons(search=order.order_number)
        assert reasons["total_complaints"] == 3
        assert reasons["total_orders_with_complaints"] == 1

        # dashboard: «заказы с жалобами» — 1 заказ, не 3 (правило #3).
        # admin_stats() глобален, поэтому сравниваем дельту: в тестовой БД
        # (сессионной и общей) могли остаться данные других тестов.
        before = DashboardService.admin_stats()["orders_with_complaints"]
        extra = clean_complaint_audit.order(regular_user, f"D-{_marker()}", fio, D0)
        clean_complaint_audit.complaint(extra, f"Ж{extra.order_number}")
        clean_complaint_audit.complaint(extra, f"Ж2-{extra.order_number}")
        db_session.commit()
        after = DashboardService.admin_stats()["orders_with_complaints"]
        assert after - before == 1, "две жалобы на один заказ = один заказ с жалобами"

    def test_join_does_not_multiply_orders(self, db_session, regular_user, clean_complaint_audit):
        """Правило #2 (D-1): 2 заказа / 4 жалобы не дают orders_count == 4."""
        fio = f"Курьер-{_marker()}"
        marker = _marker()
        first = clean_complaint_audit.order(regular_user, f"F1-{marker}", fio, D0)
        for i in range(3):
            clean_complaint_audit.complaint(first, f"Ж{marker}-{i}")
        second = clean_complaint_audit.order(regular_user, f"F2-{marker}", fio, D1)
        clean_complaint_audit.complaint(second, f"Ещё{marker}")
        db_session.commit()

        cs = {r["courier"]: r for r in AnalyticsService.courier_complaint_stats(search=marker)}
        assert cs[fio]["orders_count"] == 2, "JOIN не должен размножать заказы"
        assert cs[fio]["complaints_count"] == 4
        assert cs[fio]["complaint_rate"] == round(4 / 2 * 100, 1)

        # та же страница: complaint_couriers даёт 2 заказа, не 4
        mine = [p for p in DashboardService.complaint_couriers() if p["courier"] == fio]
        assert len(mine) == 2

        # сумма аналитики == список
        page, _ = ComplaintService.list_complaints(per_page=100, search=marker)
        assert page.total == 4


class TestSeveralOrdersSameCourier:
    """Несколько заказов одного курьера."""

    def test_courier_orders_count(self, db_session, regular_user, clean_complaint_audit):
        fio = f"Курьер-{_marker()}"
        for suffix in ("P-1", "P-2", "P-3"):
            clean_complaint_audit.order(regular_user, f"{suffix}-{_marker()}", fio, D0)
        db_session.commit()

        best = {row["fio"]: row for row in DashboardService.best_couriers(limit=50)}
        assert best[fio]["orders_count"] == 3
        assert best[fio]["orders_with_complaints_count"] == 0
        assert best[fio]["complaints_count"] == 0


class TestMixedStatuses:
    """Правило #5: статусы взаимоисключающие, сумма == общему количеству."""

    def test_status_sum_equals_total(self, db_session, regular_user, clean_complaint_audit):
        fio = f"Курьер-{_marker()}"
        marker = _marker()
        o1 = clean_complaint_audit.order(regular_user, f"ST1-{marker}", fio, D0)
        clean_complaint_audit.complaint(o1, f"А{marker}", status="открыта")
        clean_complaint_audit.complaint(o1, f"Б{marker}", status="в работе")
        o2 = clean_complaint_audit.order(regular_user, f"ST2-{marker}", fio, D1)
        clean_complaint_audit.complaint(o2, f"В{marker}", status="закрыта")
        clean_complaint_audit.complaint(o2, f"Г{marker}", status=None)
        db_session.commit()

        counts = {
            row["status"]: row["count"] for row in AnalyticsService.status_breakdown(search=marker)
        }
        assert counts == {"открыта": 1, "в работе": 1, "закрыта": 1, "none": 1}

        page, _ = ComplaintService.list_complaints(per_page=100, search=marker)
        assert page.total == sum(counts.values())

        for status, expected in [("открыта", 1), ("в работе", 1), ("закрыта", 1), ("none", 1)]:
            per_status = ComplaintService.list_complaints(
                per_page=100, search=marker, status=status
            )[0].total
            assert per_status == expected, f"фильтр status={status}"

    def test_status_filter_reaches_breakdown(self, db_session, regular_user, clean_complaint_audit):
        """D-3: фильтр статуса должен доезжать до разбивки по статусам."""
        marker = _marker()
        order = clean_complaint_audit.order(regular_user, f"FS-{marker}", f"Курьер-{_marker()}", D0)
        clean_complaint_audit.complaint(order, f"А{marker}", status="открыта")
        clean_complaint_audit.complaint(order, f"Б{marker}", status="закрыта")
        db_session.commit()

        open_rows = AnalyticsService.status_breakdown(search=marker, status="открыта")
        assert [r["count"] for r in open_rows] == [1]

        closed_rows = AnalyticsService.status_breakdown(search=marker, status="закрыта")
        assert closed_rows[0]["status"] == "закрыта"
        assert closed_rows[0]["count"] == 1

        # согласовано со списком при том же фильтре
        page, _ = ComplaintService.list_complaints(per_page=100, search=marker, status="открыта")
        assert open_rows[0]["count"] == page.total

    def test_closed_not_excluded_by_default(self, db_session, regular_user, clean_complaint_audit):
        """Правило #8: закрытые жалобы в общем количестве без явного фильтра."""
        marker = _marker()
        order = clean_complaint_audit.order(regular_user, f"CL-{marker}", f"Курьер-{_marker()}", D0)
        clean_complaint_audit.complaint(order, f"Закрыта{marker}", status="закрыта")
        db_session.commit()

        assert ComplaintService.list_complaints(per_page=100, search=marker)[0].total == 1
        cs = AnalyticsService.courier_complaint_stats(search=marker)
        assert cs[0]["complaints_count"] == 1
        assert sum(r["count"] for r in AnalyticsService.status_breakdown(search=marker)) == 1


class TestPeriodFiltering:
    """Правило #6/#7: период одинаков во всех представлениях, границы дней целы."""

    def _seed(self, audits, user):
        marker = _marker()
        fio = f"Курьер-{marker}"
        o1 = audits.order(user, f"PR1-{marker}", fio, D0)
        for i in range(3):
            audits.complaint(o1, f"Ж{marker}-{i}", created_at=D0 + timedelta(hours=i))
        o2 = audits.order(user, f"PR2-{marker}", fio, D1)
        audits.complaint(o2, f"З{marker}", status="закрыта", created_at=D1)
        o3 = audits.order(user, f"PR3-{marker}", fio, D2)
        for i in range(2):
            audits.complaint(o3, f"Жб{marker}-{i}", created_at=D2 + timedelta(hours=i))
        db.session.commit()
        return marker, fio

    @pytest.mark.parametrize(
        "date_from,date_to,expected",
        [
            ("2026-10-05", "2026-10-05", 3),  # один день
            ("2026-10-06", "2026-10-06", 1),  # один день
            ("2026-10-07", "2026-10-07", 2),  # один день
            ("2026-10-05", "2026-10-11", 6),  # неделя
            ("2026-10-06", "2026-10-07", 3),  # два дня
            ("2026-10-01", "2026-10-31", 6),  # месяц
            ("2020-01-01", "2020-01-02", 0),  # пусто
        ],
    )
    def test_all_views_share_the_period(
        self, db_session, regular_user, clean_complaint_audit, date_from, date_to, expected
    ):
        marker, _fio = self._seed(clean_complaint_audit, regular_user)
        shared = {"search": marker, "date_from": date_from, "date_to": date_to}

        list_total = ComplaintService.list_complaints(per_page=100, **shared)[0].total
        analytics = (
            sum(
                r["complaints_count"]
                for r in AnalyticsService.courier_complaint_stats(**shared).values()
            )
            if isinstance(AnalyticsService.courier_complaint_stats(**shared), dict)
            else sum(
                r["complaints_count"] for r in AnalyticsService.courier_complaint_stats(**shared)
            )
        )
        breakdown = sum(r["count"] for r in AnalyticsService.status_breakdown(**shared))
        trends = AnalyticsService.complaint_trends(**shared)["total_complaints"]
        reasons = AnalyticsService.complaint_reasons(**shared)["total_complaints"]

        assert list_total == expected
        assert trends == expected
        assert breakdown == expected
        assert reasons == expected
        assert analytics == expected

    def test_day_boundaries_do_not_lose_records(
        self, db_session, regular_user, clean_complaint_audit
    ):
        """Правило #7: жалобы 00:00 и 23:59 не теряются, соседний день — нет."""
        marker = _marker()
        fio = f"Курьер-{marker}"
        early = datetime(2026, 10, 5, 0, 0)
        late = datetime(2026, 10, 5, 23, 59, 59)
        o1 = clean_complaint_audit.order(regular_user, f"B1-{marker}", fio, D0)
        clean_complaint_audit.complaint(o1, f"00:00{marker}", created_at=early)
        clean_complaint_audit.complaint(o1, f"23:59{marker}", created_at=late)
        o2 = clean_complaint_audit.order(regular_user, f"B2-{marker}", fio, D0)
        clean_complaint_audit.complaint(o2, f"след{marker}", created_at=datetime(2026, 10, 6, 0, 0))
        db_session.commit()

        page, rows = ComplaintService.list_complaints(
            per_page=100, search=marker, date_from="2026-10-05", date_to="2026-10-05"
        )
        assert page.total == 2
        assert {row["complaint"].text for row in rows} == {f"00:00{marker}", f"23:59{marker}"}

    def test_complaint_date_not_order_date(self, db_session, regular_user, clean_complaint_audit):
        """D-2: период считается по дате жалобы, а не по дате заказа."""
        marker = _marker()
        fio = f"Курьер-{marker}"
        order = clean_complaint_audit.order(regular_user, f"LD-{marker}", fio, D0)
        clean_complaint_audit.complaint(
            order, f"Поздняя{marker}", created_at=datetime(2026, 10, 20, 12, 0)
        )
        db_session.commit()

        in_week = ComplaintService.list_complaints(
            per_page=100, search=marker, date_from="2026-10-05", date_to="2026-10-11"
        )[0].total
        later = ComplaintService.list_complaints(
            per_page=100, search=marker, date_from="2026-10-15", date_to="2026-10-25"
        )[0].total
        assert in_week == 0
        assert later == 1

        # аналитика должна вести себя так же (до фикса фильтровала Order.created_at)
        cs_week = {
            r["courier"]
            for r in AnalyticsService.courier_complaint_stats(
                search=marker, date_from="2026-10-05", date_to="2026-10-11"
            )
        }
        cs_later = {
            r["courier"]: r["complaints_count"]
            for r in AnalyticsService.courier_complaint_stats(
                search=marker, date_from="2026-10-15", date_to="2026-10-25"
            )
        }
        assert fio not in cs_week
        assert cs_later[fio] == 1


class TestMissingRelatedData:
    """Отсутствие связанного заказа или курьера."""

    def test_order_without_courier(self, db_session, regular_user, clean_complaint_audit):
        """Заказ без курьера: жалоба есть — страница не падает."""
        marker = _marker()
        order = clean_complaint_audit.order(regular_user, f"NC-{marker}", None, D0)
        clean_complaint_audit.complaint(order, f"Без курьера{marker}")
        db_session.commit()

        page, rows = ComplaintService.list_complaints(per_page=100, search=marker)
        assert page.total == 1
        assert rows[0]["order"].courier_fio is None

        # исполнитель не назначается предположительно
        summary = [fio for fio, _ in ComplaintService.courier_summary(search=marker)]
        assert all(fio and fio.strip() for fio in summary)

    def test_complaint_without_order_is_hidden_not_500(
        self, db_session, regular_user, clean_complaint_audit, admin_client
    ):
        """Сирота (order_id без заказа) не роняет страницу и не показывается."""
        clean_complaint_audit.complaint(None, "Сирота", created_at=D0, order_id=999_999_991)
        db_session.commit()

        page, rows = ComplaintService.list_complaints(per_page=100, search="Сирота")
        assert page.total == 0
        assert rows == []
        assert admin_client.get("/admin/complaints").status_code == 200

    def test_empty_selection_everywhere(self, db_session, clean_complaint_audit):
        """Пустая выборка: все представления возвращают 0/пусто без ошибок."""
        marker = f"NOSUCH{_marker()}"
        assert ComplaintService.list_complaints(per_page=100, search=marker)[0].total == 0
        assert AnalyticsService.courier_complaint_stats(search=marker) == []
        assert AnalyticsService.status_breakdown(search=marker) == []
        assert AnalyticsService.complaint_trends(search=marker)["total_complaints"] == 0
        reasons = AnalyticsService.complaint_reasons(search=marker)
        assert reasons["total_complaints"] == 0
        assert reasons["total_orders_with_complaints"] == 0


class TestCourierIsOrderExecutor:
    """Этап 4: исполнитель = курьер заказа, не автор записи."""

    def test_courier_taken_from_order_field(self, db_session, regular_user, clean_complaint_audit):
        """Курьер берётся из Order.courier_fio, а не из автора жалобы."""
        marker = _marker()
        real_courier = f"Реальный-{marker}"
        other_user = User(fio=f"Другой{marker}", token=f"DU{marker}")
        db_session.add(other_user)
        db_session.commit()

        order = clean_complaint_audit.order(regular_user, f"EX-{marker}", real_courier, D0)
        clean_complaint_audit.complaint(order, f"Ж{marker}")
        db_session.commit()

        page, rows = ComplaintService.list_complaints(per_page=100, search=marker)
        assert page.total == 1
        # автор заказа — regular_user, исполнитель — courier_fio
        assert rows[0]["order"].user_id == regular_user.id
        assert rows[0]["order"].courier_fio == real_courier
        assert rows[0]["user"].id != other_user.id

        summary = ComplaintService.courier_summary(search=marker)
        assert summary[0][0] == real_courier

        cs = AnalyticsService.courier_complaint_stats(search=marker)
        assert cs[0]["courier"] == real_courier

    def test_courier_trimmed(self, db_session, regular_user, clean_complaint_audit):
        """Значение с пробелами не создаёт «двух» курьеров."""
        marker = _marker()
        fio = f"Курьер-{marker}"
        clean_complaint_audit.order(regular_user, f"TR-{marker}", f"  {fio}  ", D0)
        order = db_session.query(Order).filter(Order.order_number == f"TR-{marker}").one()
        clean_complaint_audit.complaint(order, f"Ж{marker}")
        db_session.commit()

        page, _ = ComplaintService.list_complaints(per_page=100, courier=fio)
        assert page.total == 1

        cs = {r["courier"] for r in AnalyticsService.courier_complaint_stats(search=marker)}
        assert cs == {fio}


class TestConsistencyAcrossViews:
    """Согласованность KPI, списка, аналитики, недельного отчёта."""

    def _seed_full(self, db_session, audits, user):
        marker = _marker()
        a = f"КурьерА-{marker}"
        b = f"КурьерБ-{marker}"
        o1 = audits.order(user, f"X1-{marker}", a, D0)
        for i in range(3):
            audits.complaint(
                o1, f"Ж{marker}-{i}", status="открыта", created_at=D0 + timedelta(hours=i)
            )
        o2 = audits.order(user, f"X2-{marker}", a, D1)
        audits.complaint(o2, f"З{marker}", status="закрыта", created_at=D1)
        o3 = audits.order(user, f"X3-{marker}", b, D2)
        for i in range(2):
            audits.complaint(o3, f"Жб{marker}-{i}", status=None, created_at=D2 + timedelta(hours=i))
        db_session.commit()
        return marker, a, b

    def test_all_sources_agree(self, db_session, regular_user, clean_complaint_audit, app):
        marker, a, b = self._seed_full(db_session, clean_complaint_audit, regular_user)

        list_total = ComplaintService.list_complaints(per_page=100, search=marker)[0].total
        assert list_total == 6

        analytics = {
            r["courier"]: r for r in AnalyticsService.courier_complaint_stats(search=marker)
        }
        assert sum(r["complaints_count"] for r in analytics.values()) == list_total
        assert analytics[a]["complaints_count"] == 4
        assert analytics[b]["complaints_count"] == 2
        assert analytics[a]["orders_count"] == 2
        assert analytics[b]["orders_count"] == 1

        reasons = AnalyticsService.complaint_reasons(search=marker)
        assert reasons["total_complaints"] == list_total
        assert reasons["total_orders_with_complaints"] == 3

        assert AnalyticsService.complaint_trends(search=marker)["total_complaints"] == list_total
        assert (
            sum(r["count"] for r in AnalyticsService.status_breakdown(search=marker)) == list_total
        )

        # по курьерам — как в списке с фильтром courier=
        for fio in (a, b):
            per_courier = ComplaintService.list_complaints(
                per_page=100, search=marker, courier=fio
            )[0].total
            assert analytics[fio]["complaints_count"] == per_courier, f"расхождение для {fio}"

        # недельный отчёт: обе кривые совпадают с фактом
        with app.test_request_context():
            report = WeeklyReportService.week_report(week="2026-10-05")
        assert report["complaints_count"] == 6
        assert dict(report["daily_complaints"]) == {
            "2026-10-05": 3,
            "2026-10-06": 1,
            "2026-10-07": 2,
        }

    def test_courier_summary_matches_per_courier(
        self, db_session, regular_user, clean_complaint_audit
    ):
        """courier_summary (страница жалоб) == список с фильтром courier."""
        marker, a, b = self._seed_full(db_session, clean_complaint_audit, regular_user)

        summary = dict(ComplaintService.courier_summary(search=marker, limit=50))
        assert summary == {a: 4, b: 2}

        for fio, expected in summary.items():
            page, _ = ComplaintService.list_complaints(per_page=100, search=marker, courier=fio)
            assert page.total == expected

    def test_pg_dialect_compiles_distinct_orders(
        self, db_session, regular_user, clean_complaint_audit
    ):
        """D-1 фикс должен компилироваться под PostgreSQL."""
        from sqlalchemy import distinct, func
        from sqlalchemy.dialects import postgresql

        marker, _a, _b = self._seed_full(db_session, clean_complaint_audit, regular_user)
        query = (
            db.session.query(
                func.trim(Order.courier_fio),
                func.count(distinct(Order.id)).label("orders_count"),
                func.count(Complaint.id).label("complaints_count"),
            )
            .outerjoin(Complaint, Complaint.order_id == Order.id)
            .group_by(func.trim(Order.courier_fio))
        )
        sql = str(query.statement.compile(dialect=postgresql.dialect()))
        assert "count(DISTINCT" in sql
        assert "orders.id" in sql
