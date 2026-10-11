"""Тесты операционного анализа: обработка Excel, связывание, расчёты, качество данных."""

import io
from datetime import datetime, timedelta

import openpyxl
import pytest

from app.models.operational_analysis import (
    AssemblyOrder,
    DataQualityIssue,
    PickerMetrics,
)
from app.services.operational_analysis_service import OperationalAnalysisService


def make_excel(sheets: dict[str, tuple[list, list]]) -> io.BytesIO:
    """Создать Excel-файл в памяти из {sheet_name: (headers, rows)}."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, (headers, rows) in sheets.items():
        ws = wb.create_sheet(title=name)
        ws.append(headers)
        for row in rows:
            ws.append(row)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


class TestColumnDetection:
    """Автоопределение столбцов по ключевым словам."""

    def test_detect_order_id_variants(self):
        cases = [
            ["Номер заказа", "Другое"],
            ["Id заказа", "Другое"],
            ["ID заказа", "Другое"],
            ["order_id", "Other"],
            ["Order ID", "Other"],
        ]
        for headers in cases:
            cols = OperationalAnalysisService._detect_columns(
                headers, {"order_id": OperationalAnalysisService.ORDER_ID_KEYWORDS}
            )
            assert "order_id" in cols, f"Failed for headers: {headers}"
            assert cols["order_id"] == 0

    def test_detect_picker(self):
        headers = ["Номер заказа", "Сборщик", "Начало сборки"]
        cols = OperationalAnalysisService._detect_columns(
            headers,
            {
                "order_id": OperationalAnalysisService.ORDER_ID_KEYWORDS,
                "picker_fio": OperationalAnalysisService.PICKER_KEYWORDS,
            },
        )
        assert cols["picker_fio"] == 1

    def test_detect_start_end_times(self):
        headers = ["ID заказа", "Время начала", "Время окончания"]
        cols = OperationalAnalysisService._detect_columns(
            headers,
            {
                "order_id": OperationalAnalysisService.ORDER_ID_KEYWORDS,
                "start_time": OperationalAnalysisService.START_TIME_KEYWORDS,
                "end_time": OperationalAnalysisService.END_TIME_KEYWORDS,
            },
        )
        assert cols["start_time"] == 1
        assert cols["end_time"] == 2


class TestIdNormalization:
    """Нормализация ID заказов — сохранение значащих нулей."""

    def test_leading_zeros_preserved(self):
        assert OperationalAnalysisService._normalize_id("007") == "007"

    def test_float_integer(self):
        assert OperationalAnalysisService._normalize_id(123.0) == "123"

    def test_int(self):
        assert OperationalAnalysisService._normalize_id(42) == "42"

    def test_string_with_spaces(self):
        assert OperationalAnalysisService._normalize_id("  ABC-123  ") == "ABC-123"

    def test_none(self):
        assert OperationalAnalysisService._normalize_id(None) == ""

    def test_leading_zeros_float_from_excel(self):
        assert OperationalAnalysisService._normalize_id("00123.0") == "00123"


class TestDatetimeParsing:
    """Парсинг дат/времени из разных форматов."""

    def test_datetime_object_passthrough(self):
        dt = datetime(2026, 10, 11, 14, 30)
        assert OperationalAnalysisService._parse_datetime(dt) == dt

    def test_iso_string(self):
        result = OperationalAnalysisService._parse_datetime("2026-10-11 14:30:00")
        assert result == datetime(2026, 10, 11, 14, 30)

    def test_russian_format(self):
        result = OperationalAnalysisService._parse_datetime("11.10.2026 14:30")
        assert result == datetime(2026, 10, 11, 14, 30)

    def test_date_only(self):
        result = OperationalAnalysisService._parse_datetime("2026-10-11")
        assert result == datetime(2026, 10, 11, 0, 0)

    def test_none(self):
        assert OperationalAnalysisService._parse_datetime(None) is None

    def test_invalid_string(self):
        assert OperationalAnalysisService._parse_datetime("not a date") is None

    def test_time_only(self):
        result = OperationalAnalysisService._parse_datetime("14:30")
        assert result is not None


class TestNumberParsing:
    """Парсинг чисел из строк."""

    def test_int(self):
        assert OperationalAnalysisService._parse_number(5) == 5.0

    def test_float(self):
        assert OperationalAnalysisService._parse_number(5.5) == 5.5

    def test_comma_decimal(self):
        assert OperationalAnalysisService._parse_number("5,5") == 5.5

    def test_dot_decimal(self):
        assert OperationalAnalysisService._parse_number("5.5") == 5.5

    def test_with_text(self):
        assert OperationalAnalysisService._parse_number("5 мин") == 5.0

    def test_none(self):
        assert OperationalAnalysisService._parse_number(None) is None

    def test_empty_string(self):
        assert OperationalAnalysisService._parse_number("") is None


class TestFullAnalysis:
    """Полный анализ файла: чтение, связывание, расчёт."""

    def _make_standard_file(self, wait_threshold=30):
        """Стандартный файл с 3 листами для тестов."""
        base = datetime(2026, 10, 10, 9, 0)
        complaints_headers = ["Номер заказа", "Жалоба"]
        complaints_rows = [
            ["001", "Повреждена упаковка"],
            ["001", "Не все товары"],
            ["002", "Поздняя доставка"],
            ["999", "Заказ не найден"],
        ]
        orders_headers = ["Id заказа", "Время поступления", "Курьер", "Адрес"]
        orders_rows = [
            ["001", base, "Иванов И.И.", "ул. Ленина, 1"],
            ["002", base + timedelta(minutes=10), "Петров П.П.", "ул. Мира, 2"],
            ["003", base + timedelta(minutes=20), "Сидоров С.С.", "ул. Гагарина, 3"],
        ]
        assembly_headers = [
            "ID заказа",
            "Сборщик",
            "Время начала",
            "Время окончания",
            "Количество товаров",
        ]
        assembly_rows = [
            ["001", "Козлов А.А.", base + timedelta(minutes=5), base + timedelta(minutes=15), 5],
            ["002", "Козлов А.А.", base + timedelta(minutes=25), base + timedelta(minutes=40), 3],
            ["003", "Смирнов Б.Б.", base + timedelta(minutes=30), base + timedelta(minutes=38), 8],
            ["888", "Козлов А.А.", base, base + timedelta(minutes=5), 2],
        ]
        return make_excel(
            {
                "Жалобы": (complaints_headers, complaints_rows),
                "Заказы": (orders_headers, orders_rows),
                "Собранные заказы": (assembly_headers, assembly_rows),
            }
        )

    def test_basic_analysis(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        assert session.status == "completed"
        assert session.total_complaints == 4
        assert session.total_orders == 3
        assert session.total_assembly_orders == 4

    def test_matching_complaints_to_orders(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        # 3 жалобы сопоставлены (001 x2, 002), 1 не сопоставлена (999)
        assert session.matched_complaints == 3

    def test_matching_assembly_to_orders(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        # 3 собранных заказа сопоставлены (001, 002, 003), 1 нет (888)
        assert session.matched_assembly == 3

    def test_picker_metrics_created(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        metrics = PickerMetrics.query.filter_by(session_id=session.id).all()
        pickers = {m.picker_fio: m for m in metrics}

        assert "Козлов А.А." in pickers
        assert "Смирнов Б.Б." in pickers

        # Козлов: 2 собранных заказа (001, 002), 888 без пары в заказах
        ko = pickers["Козлов А.А."]
        assert ko.unique_orders == 2
        # 001: 10 мин, 002: 15 мин -> среднее 12.5
        assert ko.avg_assembly_time_minutes == 12.5

    def test_complaints_aggregated_per_picker(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        metrics = PickerMetrics.query.filter_by(session_id=session.id).all()
        pickers = {m.picker_fio: m for m in metrics}

        # Козлов собрал 001 (2 жалобы) и 002 (1 жалоба) = 2 заказа с жалобами, 3 жалобы
        ko = pickers["Козлов А.А."]
        assert ko.orders_with_complaints == 2
        assert ko.total_complaints == 3
        assert ko.complaint_rate == 100.0

        # Смирнов собрал 003 (без жалоб)
        sm = pickers["Смирнов Б.Б."]
        assert sm.orders_with_complaints == 0
        assert sm.total_complaints == 0
        assert sm.complaint_rate == 0.0

    def test_quality_issues_created(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        types = {i.issue_type for i in issues}

        assert "unmatched_complaint" in types  # жалоба 999
        assert "unmatched_assembly" in types  # сборка 888

    def test_assembly_orders_saved(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        # Только сопоставленные заказы сохраняются как AssemblyOrder (3 из 4)
        orders = AssemblyOrder.query.filter_by(session_id=session.id).all()
        assert len(orders) == 3

        # Проверяем длительность
        order_001 = next(o for o in orders if o.order_id == "001")
        assert order_001.assembly_duration_minutes == 10.0
        assert order_001.has_complaint is True
        assert order_001.complaint_count == 2

        # Несопоставленный (888) помечен как quality issue
        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        unmatched = [i for i in issues if i.issue_type == "unmatched_assembly"]
        assert len(unmatched) == 1
        assert unmatched[0].order_id == "888"

    def test_wait_time_calculated_from_queue(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        orders = AssemblyOrder.query.filter_by(session_id=session.id).all()
        order_001 = next(o for o in orders if o.order_id == "001")

        # Заказ в очереди: base, сборка началась: base+5мин -> ожидание 5 мин
        assert order_001.wait_time_minutes == 5.0

    def test_custom_wait_threshold(self, db_session):
        file = self._make_standard_file(wait_threshold=3)
        session = OperationalAnalysisService.analyze_file(file, wait_threshold=3)

        metrics = PickerMetrics.query.filter_by(session_id=session.id).all()
        pickers = {m.picker_fio: m for m in metrics}

        # У Козлова ожидания: 001->5 мин, 002->5 мин (оба > 3)
        ko = pickers["Козлов А.А."]
        assert ko.orders_above_wait_threshold == 2

    def test_speed_calculation(self, db_session):
        file = self._make_standard_file()
        session = OperationalAnalysisService.analyze_file(file)

        metrics = PickerMetrics.query.filter_by(session_id=session.id).all()
        pickers = {m.picker_fio: m for m in metrics}

        sm = pickers["Смирнов Б.Б."]
        # 1 заказ, 8 мин -> 1/8 = 0.125 заказов/мин
        assert sm.speed_orders_per_minute == pytest.approx(0.125, abs=0.001)
        # 8 товаров / 8 мин = 1.0 товар/мин
        assert sm.speed_items_per_minute == pytest.approx(1.0, abs=0.001)


class TestEdgeCases:
    """Граничные случаи и обработка ошибок."""

    def test_empty_file(self, db_session):
        wb = openpyxl.Workbook()
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        session = OperationalAnalysisService.analyze_file(output)
        assert session.status == "completed"
        assert session.total_orders == 0

    def test_negative_interval_flagged(self, db_session):
        base = datetime(2026, 10, 10, 9, 0)
        complaints = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        ["001", "Тест", base + timedelta(minutes=30), base],  # конец раньше начала
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(complaints)

        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        types = [i.issue_type for i in issues]
        assert "negative_interval" in types

    def test_zero_interval_flagged(self, db_session):
        base = datetime(2026, 10, 10, 9, 0)
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        ["001", "Тест", base, base],  # нулевая длительность
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)

        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        types = [i.issue_type for i in issues]
        assert "zero_interval" in types

    def test_missing_timestamps_flagged(self, db_session):
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        ["001", "Тест", None, None],
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)

        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        types = [i.issue_type for i in issues]
        assert "missing_timestamp" in types

    def test_duplicate_order_ids_flagged(self, db_session):
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"], ["001"]]),
                "Собранные заказы": (["ID заказа", "Сборщик"], [["001", "Тест"]]),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)

        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        types = [i.issue_type for i in issues]
        assert "duplicate_order" in types

    def test_unknown_picker_flagged(self, db_session):
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        ["001", "", datetime(2026, 10, 10, 9, 0), datetime(2026, 10, 10, 9, 10)],
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)

        issues = DataQualityIssue.query.filter_by(session_id=session.id).all()
        types = [i.issue_type for i in issues]
        assert "unknown_picker" in types

    def test_multiple_complaints_same_order(self, db_session):
        file = make_excel(
            {
                "Жалобы": (
                    ["Номер заказа", "Жалоба"],
                    [
                        ["001", "Жалоба 1"],
                        ["001", "Жалоба 2"],
                        ["001", "Жалоба 3"],
                    ],
                ),
                "Заказы": (["Id заказа"], [["001"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        [
                            "001",
                            "Тест",
                            datetime(2026, 10, 10, 9, 0),
                            datetime(2026, 10, 10, 9, 10),
                        ],
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)

        orders = AssemblyOrder.query.filter_by(session_id=session.id).all()
        order = orders[0]
        assert order.complaint_count == 3
        assert order.has_complaint is True

    def test_missing_sheets(self, db_session):
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)
        assert session.status == "completed"
        assert session.total_orders == 0
        assert session.total_assembly_orders == 0

    def test_no_wait_time_no_substitution(self, db_session):
        """Если нет меток ожидания — показать, а не подменять."""
        base = datetime(2026, 10, 10, 9, 0)
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"]]),  # нет queue_time
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        ["001", "Тест", base, base + timedelta(minutes=10)],
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)

        orders = AssemblyOrder.query.filter_by(session_id=session.id).all()
        order = orders[0]
        # Ожидание не рассчитано — None, а не подменено
        assert order.wait_time_minutes is None


class TestFiltering:
    """Фильтрация результатов."""

    def _create_session(self, db_session):
        base = datetime(2026, 10, 10, 9, 0)
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"], ["002"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        ["001", "Козлов", base, base + timedelta(minutes=10)],
                        [
                            "002",
                            "Смирнов",
                            base + timedelta(hours=10),
                            base + timedelta(hours=10, minutes=5),
                        ],
                    ],
                ),
            }
        )
        return OperationalAnalysisService.analyze_file(file)

    def test_filter_by_picker(self, db_session):
        session = self._create_session(db_session)
        results = OperationalAnalysisService.filter_results(session.id, picker="Козлов")
        assert len(results["assembly_orders"]) == 1
        assert results["assembly_orders"][0].picker_fio == "Козлов"

    def test_filter_by_complaint(self, db_session):
        session = self._create_session(db_session)
        results = OperationalAnalysisService.filter_results(session.id, has_complaint="yes")
        assert len(results["assembly_orders"]) == 1
        assert results["assembly_orders"][0].order_id == "001"

    def test_filter_by_time_of_day(self, db_session):
        session = self._create_session(db_session)
        # 001 собран в 9:00 (утро), 002 в 19:00 (вечер)
        results = OperationalAnalysisService.filter_results(session.id, time_of_day="morning")
        assert len(results["assembly_orders"]) == 1
        assert results["assembly_orders"][0].order_id == "001"

    def test_filter_by_date(self, db_session):
        session = self._create_session(db_session)
        results = OperationalAnalysisService.filter_results(
            session.id, date_from="2026-10-10", date_to="2026-10-10"
        )
        assert len(results["assembly_orders"]) == 2


class TestExport:
    """Экспорт в Excel."""

    def test_export_creates_file(self, db_session):
        file = make_excel(
            {
                "Жалобы": (["Номер заказа"], [["001"]]),
                "Заказы": (["Id заказа"], [["001"]]),
                "Собранные заказы": (
                    ["ID заказа", "Сборщик", "Время начала", "Время окончания"],
                    [
                        [
                            "001",
                            "Тест",
                            datetime(2026, 10, 10, 9, 0),
                            datetime(2026, 10, 10, 9, 10),
                        ],
                    ],
                ),
            }
        )
        session = OperationalAnalysisService.analyze_file(file)
        excel_data = OperationalAnalysisService.export_excel(session.id)

        assert len(excel_data) > 0
        # Проверяем, что это валидный xlsx
        wb = openpyxl.load_workbook(io.BytesIO(excel_data))
        assert "Сводка по сборщикам" in wb.sheetnames
        assert "Детализация заказов" in wb.sheetnames
        assert "Контроль качества" in wb.sheetnames


class TestRoutes:
    """Тесты маршрутов (smoke)."""

    def test_analysis_page_requires_admin(self, client):
        resp = client.get("/admin/operational_analysis")
        assert resp.status_code == 302

    def test_analysis_page_with_admin(self, admin_client):
        resp = admin_client.get("/admin/operational_analysis")
        assert resp.status_code == 200
        assert "Операционный анализ" in resp.get_data(as_text=True)

    def test_upload_rejects_xls(self, admin_client):
        data = {
            "excel_file": (io.BytesIO(b"fake"), "test.xls"),
        }
        resp = admin_client.post(
            "/admin/operational_analysis/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302

    def test_upload_requires_file(self, admin_client):
        resp = admin_client.post("/admin/operational_analysis/upload")
        assert resp.status_code == 302
