import io

import openpyxl

from app.models import ImportMapping, Order, User
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

    def test_preview_import_new_orders(self, db_session, regular_user):
        """Превью показывает новые заказы без сохранения."""
        mapping = ImportMapping(
            name="Test Preview",
            type="orders",
            id_column="ID",
            fio_column="ФИО",
            order_number_column="Номер заказа",
            field_mapping={"Описание": "description"},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = self.create_excel_file(
            headers=["ID", "ФИО", "Номер заказа", "Описание"],
            rows=[
                ["PVW-NEW-EXT-ff92fc7d-1", regular_user.fio, "PVW-001", "Desc 1"],
                ["PVW-NEW-EXT-ff92fc7d-2", regular_user.fio, "PVW-002", "Desc 2"],
            ],
        )

        preview = ImportService.preview_import(file, mapping)

        # Check that our specific rows are categorized as new
        new_rows = preview["new"]["rows"]
        our_new = [
            r
            for r in new_rows
            if r["external_id"] in ("PVW-NEW-EXT-ff92fc7d-1", "PVW-NEW-EXT-ff92fc7d-2")
        ]
        assert len(our_new) == 2, f"Expected 2 new rows, got {len(our_new)}: {new_rows}"
        for r in our_new:
            assert r["action"] == "create"
            assert r["user_id"] == regular_user.id
        # Total counts may be higher due to test pollution, but our rows should be new
        assert preview["new"]["count"] >= 2
        assert preview["has_data"] is True

    def test_preview_import_update_existing(self, db_session, regular_user):
        """Превью показывает обновляемые заказы."""
        mapping = ImportMapping(
            name="Test Preview Update",
            type="orders",
            id_column="ID",
            fio_column="ФИО",
            order_number_column="Номер заказа",
            field_mapping={},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        # Сначала создаём заказ с уникальным external_id
        unique_ext_id = "PVW-UPD-EXT-unique-123"
        existing = Order(
            user_id=regular_user.id,
            order_number="PVW-UPD-001",
            external_id=unique_ext_id,
            description="Old",
        )
        db_session.add(existing)
        db_session.commit()

        file = self.create_excel_file(
            headers=["ID", "ФИО", "Номер заказа"],
            rows=[
                [unique_ext_id, regular_user.fio, "PVW-UPD-002"],
            ],
        )

        preview = ImportService.preview_import(file, mapping)

        # Find our specific updated row - external_id is the ID column value
        updated_rows = preview["updated"]["rows"]
        our_updated = [
            r
            for r in updated_rows
            if r.get("external_id") == unique_ext_id and r.get("new_order_number") == "PVW-UPD-002"
        ]
        assert len(our_updated) == 1, f"Expected our updated row, got {updated_rows}"
        row = our_updated[0]
        assert row["action"] == "update"
        assert row["existing_id"] == existing.id
        assert row["new_order_number"] == "PVW-UPD-002"

    def test_preview_import_duplicate_in_file(self, db_session, regular_user):
        """Превью находит дубликаты ID в самом файле."""
        mapping = ImportMapping(
            name="Test Preview Dup",
            type="orders",
            id_column="ID",
            fio_column="ФИО",
            order_number_column="Номер заказа",
            field_mapping={},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = self.create_excel_file(
            headers=["ID", "ФИО", "Номер заказа"],
            rows=[
                ["PVW-DUP-EXT-554ef594-1", regular_user.fio, "PVW-DUP-001"],
                ["PVW-DUP-EXT-554ef594-1", regular_user.fio, "PVW-DUP-002"],  # тот же ID
            ],
        )

        preview = ImportService.preview_import(file, mapping)

        # Check that our first row is new, second is duplicate
        new_rows = preview["new"]["rows"]
        our_new = [
            r
            for r in new_rows
            if r.get("external_id") == "PVW-DUP-EXT-554ef594-1" and r.get("action") == "create"
        ]
        assert len(our_new) == 1, f"Expected 1 new row for our ID, got {len(our_new)}: {new_rows}"

        dup_rows = preview["duplicates"]["rows"]
        our_dup = [r for r in dup_rows if r.get("external_id") == "PVW-DUP-EXT-554ef594-1"]
        assert len(our_dup) == 1, f"Expected 1 duplicate for our ID, got {len(our_dup)}: {dup_rows}"
        assert "Дубликат ID в файле" in our_dup[0]["reason"]

    def test_preview_import_unknown_user(self, db_session, regular_user):
        """Превью помечает неизвестного пользователя как unmatchable."""
        mapping = ImportMapping(
            name="Test Preview Unknown",
            type="orders",
            id_column="ID",
            fio_column="ФИО",
            order_number_column="Номер заказа",
            field_mapping={},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        file = self.create_excel_file(
            headers=["ID", "ФИО", "Номер заказа"],
            rows=[
                ["1", "Unknown User", "ORDER-001"],
            ],
        )

        preview = ImportService.preview_import(file, mapping)

        assert preview["unmatchable"]["count"] == 1
        assert preview["new"]["count"] == 0
        unmatch = preview["unmatchable"]["rows"][0]
        assert "не найден" in unmatch["reason"]

    def test_preview_import_conflict_order_number(self, db_session, regular_user):
        """Превью находит конфликт номера заказа у того же пользователя (другой ID)."""
        mapping = ImportMapping(
            name="Test Preview Conflict",
            type="orders",
            id_column="ID",
            fio_column="ФИО",
            order_number_column="Номер заказа",
            field_mapping={},
            skip_first_row=True,
        )
        db_session.add(mapping)
        db_session.commit()

        # Создаём заказ ТОГО ЖЕ пользователя с таким номером, но ДРУГИМ external_id
        existing_order = Order(
            user_id=regular_user.id,
            order_number="PVW-CONF-001",
            external_id="OTHER-EXT-ID-999",
        )
        db_session.add(existing_order)
        db_session.commit()

        # Пытаемся импортировать заказ с ТЕМ ЖЕ номером, но НОВЫМ external_id
        file = self.create_excel_file(
            headers=["ID", "ФИО", "Номер заказа"],
            rows=[
                ["PVW-CONF-EXT-bb72dd25", regular_user.fio, "PVW-CONF-001"],
            ],
        )

        preview = ImportService.preview_import(file, mapping)

        # Find our specific conflict - external_id is "PVW-CONF-EXT-bb72dd25"
        conflict_rows = preview["conflicts"]["rows"]
        our_conflict = [r for r in conflict_rows if r.get("external_id") == "PVW-CONF-EXT-bb72dd25"]
        assert len(our_conflict) == 1, f"Expected our conflict row, got {conflict_rows}"
        assert "уже существует" in our_conflict[0]["reason"]
