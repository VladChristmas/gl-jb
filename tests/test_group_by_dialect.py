"""Регрессии на дефекты живого аудита production (2026-10-12).

F-01/F-02: на production PostgreSQL падало `/admin/complaints` и
`/admin/analytics` с GroupingError: SELECT содержал голую колонку
`orders.courier_fio`, а GROUP BY — её выражение `trim(orders.courier_fio)`.
SQLite (на котором гоняются все тесты) такие запросы разрешает, поэтому
баг не ловился. Компилируем те же запросы под postgresql-диалект и
проверяем, что в SELECT нет голой колонки вне агрегатов.

F-03: `/admin/weekly_report` падал с jinja UndefinedError — карточки kpi_cards
без ключей `pct`/`delta`. Шаблон `{% if card.pct is not none %}` пропускал
Undefined (Undefined != None) и падал на format(). Рендер страницы с пустыми
данными — регрессия на любой СУБД.
"""

import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query

PG_WHERE_TRIM = re.compile(r"trim\(orders\.courier_fio\)")
_SELECT_CLAUSE = re.compile(r"SELECT\s+(.*?)\s+FROM\s", re.DOTALL)


def _pg_sql(monkeypatch):
    """Собираем SQL всех запросов сервиса (без обращения к БД)."""
    queries: list[str] = []

    def fake_all(self):
        queries.append(str(self.statement.compile(dialect=postgresql.dialect())))
        return []

    monkeypatch.setattr(Query, "all", fake_all)
    return queries


def _courier_grouping_sql(queries: list[str]) -> str:
    """SQL агрегации по курьерам — единственный запрос с GROUP BY trim(courier_fio)."""
    for sql in queries:
        if PG_WHERE_TRIM.search(sql):
            return sql
    raise AssertionError(f"Не найден запрос с GROUP BY trim(courier_fio): {queries}")


def _assert_no_bare_courier_fio(sql: str):
    """В SELECT-списке не должно оставаться голой orders.courier_fio.

    Голая колонка в WHERE/GROUP BY — легальна; GroupingError возникает, когда
    SELECT содержит колонку, которой нет в GROUP BY целиком (а не её trim()).
    """
    match = _SELECT_CLAUSE.search(sql)
    assert match, f"Не удалось выделить SELECT-список: {sql}"
    select_list = PG_WHERE_TRIM.sub("", match.group(1))
    assert (
        "orders.courier_fio" not in select_list
    ), f"SELECT содержит голую колонку вне GROUP BY-выражения: {sql}"


class TestCourierSummaryGroupBy:
    def test_pg_dialect_select_matches_group_by(self, monkeypatch):
        """courier_summary: SELECT trim(...) + GROUP BY trim(...) — валидно для PG."""
        from app.services.complaint_service import ComplaintService

        queries = _pg_sql(monkeypatch)
        assert ComplaintService.courier_summary(search="x") == []

        sql = _courier_grouping_sql(queries)
        _assert_no_bare_courier_fio(sql)


class TestAnalyticsGroupBy:
    def test_courier_complaint_stats_pg_dialect(self, monkeypatch):
        from app.services.dashboard_service import AnalyticsService

        queries = _pg_sql(monkeypatch)
        assert AnalyticsService.courier_complaint_stats(search="x") == []

        sql = _courier_grouping_sql(queries)
        _assert_no_bare_courier_fio(sql)

    def test_get_filter_options_pg_dialect(self, monkeypatch):
        from app.services.dashboard_service import AnalyticsService

        queries = _pg_sql(monkeypatch)
        AnalyticsService.get_filter_options()

        sql = _courier_grouping_sql(queries)
        _assert_no_bare_courier_fio(sql)


class TestWeeklyReportRender:
    def test_weekly_report_renders_200(self, admin_client):
        """F-03: kpi_cards без ключей pct роняли шаблон на любой СУБД."""
        resp = admin_client.get("/admin/weekly_report")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Ключевые показатели недели" in html
        assert "Заказов" in html
        assert "Жалоб" in html

    def test_weekly_report_prev_week_renders(self, admin_client):
        resp = admin_client.get(
            "/admin/weekly_report",
            query_string={"week": "2026-09-28"},
        )
        assert resp.status_code == 200

    def test_weekly_report_export_xlsx(self, admin_client):
        resp = admin_client.get("/admin/weekly_report/export_excel")
        assert resp.status_code == 200
        assert "spreadsheet" in resp.headers.get("Content-Type", "")


class TestComplaintsAnalyticsRender:
    def test_complaints_renders_200(self, admin_client):
        """F-01: GROUP BY trim ронял список жалоб на PostgreSQL."""
        resp = admin_client.get("/admin/complaints")
        assert resp.status_code == 200

    def test_analytics_renders_200(self, admin_client):
        """F-02: GROUP BY trim ронял аналитику на PostgreSQL."""
        resp = admin_client.get("/admin/analytics")
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "query",
        [
            {"search": "тест"},
            {"status": "открыта"},
            {"period": "week"},
            {"date_from": "2026-10-01", "date_to": "2026-10-12"},
            {"page": "2"},
        ],
    )
    def test_complaints_with_filters_renders_200(self, admin_client, query):
        resp = admin_client.get("/admin/complaints", query_string=query)
        assert resp.status_code == 200
