import io

import openpyxl

from app.models import ImportMapping, Order
from app.services.import_service import ImportService


class TestImportService:
    def create_excel_file(self, headers, rows):
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

    def test_import_orders_from_excel(self, db_session, regular_user):
        # Create Excel file with the exact FIO from the fixture
        file = self.create_excel_file(
            headers=["ФИО", "Номер заказа", "Описание"],
            rows=[
                [regular_user.fio, "ORDER-001", "Description 1"],
            ],
        )

        result = ImportService.import_orders_from_excel(file, fio_col_idx=0)

        assert result["imported"] == 1
        assert result["skipped"] == 0
        assert result["duplicates"] == 0

        # Verify order was created
        order = Order.query.filter_by(order_number="ORDER-001").first()
        assert order is not None
        assert order.user_id == regular_user.id
        assert order.description == "Номер заказа: ORDER-001; Описание: Description 1"

    def test_import_orders_skip_unknown_user(self, db_session, regular_user):
        file = self.create_excel_file(
            headers=["ФИО", "Номер заказа"],
            rows=[
                ["Unknown User", "ORDER-001"],
            ],
        )

        result = ImportService.import_orders_from_excel(file, fio_col_idx=0)

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert "не найден" in result["errors"][0]

    def test_import_with_mapping(self, db_session, regular_user):
        # Create import mapping
        mapping = ImportMapping(
            name="Test Mapping",
            type="orders",
            id_column="ID",
            fio_column="ФИО",
            order_number_column="Номер заказа",
            field_mapping={"Описание": "description"},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        # Create Excel file with the exact FIO from the fixture
        file = self.create_excel_file(
            headers=["ID", "ФИО", "Номер заказа", "Описание"],
            rows=[
                ["1", regular_user.fio, "ORDER-001", "Test description"],
            ],
        )

        result = ImportService.import_with_mapping(file, mapping)

        assert result["imported"] == 1
        assert result["updated"] == 0

        # Verify order was created with external_id
        order = Order.query.filter_by(external_id="1").first()
        assert order is not None
        assert order.order_number == "ORDER-001"
        assert order.user_id == regular_user.id
