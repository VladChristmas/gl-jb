"""Импорт ODS-файла операционных данных через OperationalAnalysisService.

Конвертирует ODS -> XLSX в памяти и запускает полный анализ:
чтение 3 листов (жалобы/заказы/сборка), связывание, расчёт метрик сборщиков.

Использование:
    python scripts/import_ods.py C:\\path\\to\\file.ods [--wait-threshold 30]
"""

import argparse
import io
import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def ods_to_xlsx_bytes(ods_path: str) -> bytes:
    """Читает ODS через pandas/odfpy, пишет XLSX через openpyxl. Возвращает bytes."""
    xl = pd.ExcelFile(ods_path, engine="odf")
    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name in xl.sheet_names:
        # dtype=str: без него pandas приводит текстовые ID вида "001" к int 1
        # (потеря ведущих нулей -> рассинхрон связывания между листами)
        df = pd.read_excel(xl, sheet_name=sheet_name, header=0, dtype=str)
        ws = wb.create_sheet(title=sheet_name)
        ws.append([str(c) if c is not None else "" for c in df.columns])
        for row in df.itertuples(index=False, name=None):
            cleaned = []
            for v in row:
                if pd.isna(v):
                    cleaned.append(None)
                elif hasattr(v, "to_pydatetime"):
                    cleaned.append(v.to_pydatetime())
                else:
                    cleaned.append(v)
            ws.append(cleaned)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт ODS в operational analysis")
    parser.add_argument("ods_path", help="Путь к .ods файлу")
    parser.add_argument(
        "--wait-threshold", type=int, default=None, help="Порог ожидания, мин (по умолчанию 30)"
    )
    args = parser.parse_args()

    ods_path = Path(args.ods_path)
    if not ods_path.exists():
        print(f"Файл не найден: {ods_path}")
        sys.exit(1)

    print(f"Читаю ODS: {ods_path}")
    try:
        xlsx_bytes = ods_to_xlsx_bytes(str(ods_path))
    except Exception as exc:  # noqa: BLE001 - единая точка обработки ошибок CLI
        print(f"Не удалось прочитать ODS-файл: {type(exc).__name__}: {exc}")
        print("Проверьте, что файл является корректным OpenDocument Spreadsheet (.ods).")
        sys.exit(1)
    print(f"Конвертировано в XLSX: {len(xlsx_bytes)} байт")

    from app import create_app
    from app.extensions import db
    from app.services.operational_analysis_service import OperationalAnalysisService

    app = create_app()
    with app.app_context():
        buf = io.BytesIO(xlsx_bytes)
        buf.filename = ods_path.name  # type: ignore[attr-defined]
        try:
            session = OperationalAnalysisService.analyze_file(
                buf, wait_threshold=args.wait_threshold
            )
        except Exception as exc:  # noqa: BLE001 - единая точка обработки ошибок CLI
            # Откатываем незавершённую сессию, чтобы в истории не осталось status=processing
            db.session.rollback()
            print(f"Анализ не завершён: {type(exc).__name__}: {exc}")
            print("Результаты не сохранены.")
            sys.exit(1)

        if session.status != "completed":
            print(f"Анализ завершился со статусом: {session.status}")
            sys.exit(1)

        print(f"\n=== Сессия #{session.id}: {session.filename} ===")
        print(f"Статус: {session.status}")
        print(f"Жалоб: {session.total_complaints} (сопоставлено: {session.matched_complaints})")
        print(f"Заказов: {session.total_orders} (сопоставлено: {session.matched_orders})")
        print(f"Сборок: {session.total_assembly_orders} (сопоставлено: {session.matched_assembly})")
        print(f"Ошибок: {session.error_count}, предупреждений: {session.warning_count}")

        from app.models.operational_analysis import PickerMetrics

        metrics = (
            PickerMetrics.query.filter_by(session_id=session.id)
            .order_by(PickerMetrics.unique_orders.desc())
            .all()
        )
        print(f"\nСборщиков: {len(metrics)}")
        for m in metrics[:15]:
            print(
                f"  {m.picker_fio}: заказов={m.unique_orders}, "
                f"средн. сборка={m.avg_assembly_time_minutes:.1f} мин, "
                f"скорость={m.speed_orders_per_minute:.2f} зак/мин, "
                f"жалоб={m.total_complaints}"
            )
        if len(metrics) > 15:
            print(f"  ... и ещё {len(metrics) - 15}")

        print(f"\nГотово. Результаты: /admin/operational_analysis/{session.id}")


if __name__ == "__main__":
    main()
