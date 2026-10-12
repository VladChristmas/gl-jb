"""P3-2: скачиваемый отчёт об ошибках импорта.

Раньше в flash попадало только первые 5 ошибок, остальные были недоступны —
оператор не мог понять, что именно не импортировалось.
"""

import io
import uuid
from pathlib import Path

import pytest

from app.models import ImportMapping
from app.services.import_service import ImportService


@pytest.fixture
def import_mapping(db_session):
    mapping = ImportMapping(
        name=f"report-mapping-{uuid.uuid4().hex[:8]}",
        type="orders",
        id_column="ID",
        fio_column="ФИО",
        order_number_column="Номер заказа",
        field_mapping={},
        skip_first_row=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


def _post_import(admin_client, mapping, filename="orders.xlsx"):
    return admin_client.post(
        "/admin/import_excel",
        data={
            "mapping_id": str(mapping.id),
            "excel_file": (io.BytesIO(b"PK\x03\x04fake"), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


class TestImportReport:
    def test_errors_are_downloadable(self, admin_client, import_mapping, monkeypatch):
        errors = [
            'Строка 2: пользователь "Иванов Иван" не найден',
            "Строка 5: пустое ФИО",
            'Строка 9: заказ с ID "999" не найден',
        ]

        def fake_import(file, mapping):
            return {"imported": 1, "updated": 0, "skipped": 0, "errors": errors}

        monkeypatch.setattr(ImportService, "import_with_mapping", staticmethod(fake_import))

        page = _post_import(admin_client, import_mapping)
        assert "/admin/import_report" in page.get_data(as_text=True)

        resp = admin_client.get("/admin/import_report")

        assert resp.status_code == 200
        disposition = resp.headers["Content-Disposition"]
        assert disposition.startswith("attachment;")
        assert "import_errors_" in disposition

        body = resp.get_data().decode("utf-8-sig")
        assert "Всего ошибок: 3" in body
        assert "Создано: 1, обновлено: 0, пропущено: 0" in body
        for error in errors:
            assert error in body

    def test_report_is_capped_with_summary(self, admin_client, import_mapping, monkeypatch):
        errors = [f"Строка {index}: ошибка" for index in range(1500)]

        def fake_import(file, mapping):
            return {"imported": 0, "updated": 0, "skipped": 1500, "errors": errors}

        monkeypatch.setattr(ImportService, "import_with_mapping", staticmethod(fake_import))

        _post_import(admin_client, import_mapping)
        body = admin_client.get("/admin/import_report").get_data().decode("utf-8-sig")

        assert "Всего ошибок: 1500" in body
        assert "1. Строка 0: ошибка" in body
        assert "1000. Строка 999: ошибка" in body
        assert "Строка 1499: ошибка" not in body
        assert "и ещё 500 ошибок" in body

    def test_clean_import_clears_previous_report(self, admin_client, import_mapping, monkeypatch):
        monkeypatch.setattr(
            ImportService,
            "import_with_mapping",
            staticmethod(
                lambda file, mapping: {
                    "imported": 0,
                    "updated": 0,
                    "skipped": 0,
                    "errors": ["Строка 2: ошибка"],
                }
            ),
        )
        _post_import(admin_client, import_mapping)
        assert admin_client.get("/admin/import_report").status_code == 200

        monkeypatch.setattr(
            ImportService,
            "import_with_mapping",
            staticmethod(
                lambda file, mapping: {
                    "imported": 5,
                    "updated": 0,
                    "skipped": 0,
                    "errors": [],
                }
            ),
        )
        _post_import(admin_client, import_mapping)

        resp = admin_client.get("/admin/import_report")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin/import_excel")

    def test_missing_report_redirects_to_import_page(self, admin_client):
        resp = admin_client.get("/admin/import_report")

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin/import_excel")

    def test_report_requires_admin(self, client):
        resp = client.get("/admin/import_report")

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin/login")

    def test_rejected_file_does_not_create_report(self, admin_client):
        admin_client.post(
            "/admin/import_excel",
            data={
                "mapping_id": "0",
                "excel_file": (io.BytesIO(b"x"), "orders.txt"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

        assert admin_client.get("/admin/import_report").status_code == 302

    def test_session_cookie_stays_within_browser_limit(
        self, admin_client, app, import_mapping, monkeypatch
    ):
        """P2: полный отчёт об ошибках не должен попадать в session-cookie.

        Кука > 4093 байт молча отбрасывается реальными браузерами (werkzeug
        предупреждает): ссылка на скачивание и блок отчёта пропадали.
        """
        errors = [f"Строка {index}: ошибка {index}" for index in range(1500)]

        monkeypatch.setattr(
            ImportService,
            "import_with_mapping",
            staticmethod(
                lambda file, mapping: {
                    "imported": 0,
                    "updated": 0,
                    "skipped": 1500,
                    "errors": errors,
                }
            ),
        )

        resp = admin_client.post(
            "/admin/import_excel",
            data={
                "mapping_id": str(import_mapping.id),
                "excel_file": (io.BytesIO(b"PK\x03\x04fake"), "orders.xlsx"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "session=" in set_cookie, "Сессионная кука не установлена"
        cookie_value = set_cookie.split("session=", 1)[1].split(";", 1)[0]
        assert len(cookie_value) <= 4093, (
            f"session-cookie {len(cookie_value)} байт > лимита 4093: "
            "браузер молча отбросит отчёт"
        )

        # Отчёт хранится на диске и удаляется после скачивания
        report_dir = Path(app.instance_path) / "import_reports"
        with admin_client.session_transaction() as sess:
            report_id = sess["import_report"]["id"]
        report_path = report_dir / f"{report_id}.json"
        assert report_path.exists(), "Отчёт не сохранён на диск"

        body = admin_client.get("/admin/import_report").get_data().decode("utf-8-sig")
        assert "Всего ошибок: 1500" in body
        assert "1000. Строка 999: ошибка 999" in body
        assert "... и ещё 500 ошибок" in body
        assert not report_path.exists(), "Файл отчёта не удалён"
