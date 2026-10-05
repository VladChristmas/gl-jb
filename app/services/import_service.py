from datetime import datetime

import openpyxl
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models.import_mapping import ImportMapping
from app.models.order import Order
from app.services.user_service import UserService


class ImportService:
    @staticmethod
    def import_orders_from_excel(file: FileStorage, fio_col_idx: int) -> dict:
        workbook = openpyxl.load_workbook(file)
        sheet = workbook.active

        headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        users_map = UserService.get_users_map()

        imported = 0
        skipped = 0
        duplicates = 0
        errors = []

        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue

            fio_value = row[fio_col_idx]
            if not fio_value:
                skipped += 1
                errors.append(f"Строка {row_idx}: пустое ФИО")
                continue

            fio_normalized = str(fio_value).strip().lower()
            user_id = users_map.get(fio_normalized)

            if not user_id:
                skipped += 1
                errors.append(f'Строка {row_idx}: пользователь "{fio_value}" не найден')
                continue

            order_data = {}
            for idx, header in enumerate(headers):
                if idx != fio_col_idx and header:
                    order_data[str(header).strip()] = row[idx]

            order_number = (
                order_data.get("Номер заказа")
                or order_data.get("order_number")
                or f"Заказ #{row_idx}"
            )

            existing = Order.query.filter_by(user_id=user_id, order_number=order_number).first()
            if existing:
                duplicates += 1
                errors.append(
                    f'Строка {row_idx}: заказ "{order_number}" уже существует для этого пользователя'
                )
                continue

            description_parts = []
            for key, value in order_data.items():
                if value is not None:
                    description_parts.append(f"{key}: {value}")
            description = "; ".join(description_parts)

            order = Order(user_id=user_id, order_number=order_number, description=description)
            db.session.add(order)
            imported += 1

        db.session.commit()

        return {
            "imported": imported,
            "skipped": skipped,
            "duplicates": duplicates,
            "errors": errors,
        }

    @staticmethod
    def import_with_mapping(file: FileStorage, mapping: ImportMapping) -> dict:
        workbook = openpyxl.load_workbook(file)
        sheet = workbook.active

        headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        headers = [str(h).strip() if h else "" for h in headers]

        id_col_idx = None
        for idx, header in enumerate(headers):
            if header.lower() == mapping.id_column.lower():
                id_col_idx = idx
                break

        if id_col_idx is None:
            raise ValueError(
                f'Колонка ID "{mapping.id_column}" не найдена в файле. Доступные: {headers}'
            )

        fio_col_idx = None
        if mapping.fio_column:
            for idx, header in enumerate(headers):
                if header.lower() == mapping.fio_column.lower():
                    fio_col_idx = idx
                    break

        order_num_col_idx = None
        if mapping.order_number_column:
            for idx, header in enumerate(headers):
                if header.lower() == mapping.order_number_column.lower():
                    order_num_col_idx = idx
                    break

        field_indices = {}
        for file_col, model_field in mapping.field_mapping.items():
            for idx, header in enumerate(headers):
                if header.lower() == file_col.lower():
                    field_indices[model_field] = idx
                    break

        users_map = UserService.get_users_map()

        imported = 0
        updated = 0
        skipped = 0
        errors = []

        start_row = 2 if mapping.skip_first_row else 1

        for row_idx, row in enumerate(
            sheet.iter_rows(min_row=start_row, values_only=True), start=start_row
        ):
            if not any(row):
                continue

            ext_id = row[id_col_idx]
            if not ext_id:
                skipped += 1
                errors.append(f"Строка {row_idx}: пустой ID")
                continue

            ext_id = str(ext_id).strip()

            if mapping.type == "orders":
                fio_value = row[fio_col_idx] if fio_col_idx is not None else None
                if not fio_value:
                    skipped += 1
                    errors.append(f"Строка {row_idx}: пустое ФИО")
                    continue

                fio_normalized = str(fio_value).strip().lower()
                user_id = users_map.get(fio_normalized)
                if not user_id:
                    skipped += 1
                    errors.append(f'Строка {row_idx}: пользователь "{fio_value}" не найден')
                    continue

                order_number = (
                    row[order_num_col_idx] if order_num_col_idx is not None else f"Заказ #{row_idx}"
                )
                if not order_number:
                    order_number = f"Заказ #{row_idx}"

                order = Order.query.filter_by(external_id=ext_id).first()

                description_parts = []
                for model_field, col_idx in field_indices.items():
                    value = row[col_idx]
                    if value is not None:
                        description_parts.append(f"{model_field}: {value}")
                description = "; ".join(description_parts)

                if order:
                    order.user_id = user_id
                    order.order_number = order_number
                    order.description = description
                    order.external_id = ext_id
                    updated += 1
                else:
                    order = Order(
                        user_id=user_id,
                        order_number=order_number,
                        description=description,
                        external_id=ext_id,
                    )
                    db.session.add(order)
                    imported += 1

            elif mapping.type == "complaints":
                order = Order.query.filter_by(external_id=ext_id).first()
                if not order:
                    skipped += 1
                    errors.append(f'Строка {row_idx}: заказ с ID "{ext_id}" не найден')
                    continue

                for model_field, col_idx in field_indices.items():
                    value = row[col_idx]
                    if value is not None:
                        if model_field == "complaint_text":
                            order.complaint_text = str(value)
                        elif model_field == "complaint_status":
                            order.complaint_status = str(value)
                        elif model_field == "complaint_date":
                            if isinstance(value, str):
                                try:
                                    order.complaint_date = datetime.fromisoformat(
                                        value.replace("Z", "+00:00")
                                    )
                                except Exception:
                                    pass
                            elif hasattr(value, "isoformat"):
                                order.complaint_date = value

                updated += 1

        db.session.commit()

        return {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors}
