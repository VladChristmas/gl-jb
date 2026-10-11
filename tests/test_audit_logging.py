"""Аудит-события (P3-4): login success/failure, import start/end, complaint update.

Проверяем, что события реально пишутся, что секреты (токен/пароль) в лог
не попадают, а имя файла/текст жалобы нельзя использовать для log forging.
"""

import io
import logging

import pytest

from app.extensions import limiter
from app.models import ImportMapping
from app.services.complaint_service import ComplaintService
from app.services.import_service import ImportService
from config import ADMIN_PASSWORD

AUDIT_LOGGER = "gl_jb.audit"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


class TestLoginAudit:
    def test_unified_login_success_is_logged(self, client, regular_user, caplog):
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            resp = client.post("/login", data={"token": regular_user.token})

        assert resp.status_code == 302
        assert any(
            message.startswith("login success method=unified")
            and f"user_id={regular_user.id}" in message
            for message in caplog.messages
        )

    def test_unified_login_failure_does_not_leak_token(self, client, caplog):
        secret = "SUPER-SECRET-TOKEN-DO-NOT-LOG"

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            resp = client.post("/login", data={"token": secret})

        assert resp.status_code == 200
        joined = "\n".join(caplog.messages)
        assert "login failed method=unified reason=invalid_token" in joined
        assert secret not in joined, "токен попал в лог"
        assert "attempt_len=29" in joined

    def test_admin_password_login_is_logged(self, client, caplog):
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            client.post("/admin/login", data={"password": "wrong-password"})
            client.post("/admin/login", data={"password": ADMIN_PASSWORD})

        joined = "\n".join(caplog.messages)
        assert "login failed method=password reason=invalid_password" in joined
        assert "login success method=password role=admin" in joined
        assert "wrong-password" not in joined
        assert ADMIN_PASSWORD not in joined

    def test_empty_token_is_logged_as_failure(self, client, caplog):
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            client.post("/login", data={"token": ""})

        assert any(
            message.startswith("login failed method=unified reason=empty_token")
            for message in caplog.messages
        )


class TestImportAudit:
    @pytest.fixture
    def import_mapping(self, db_session):
        import uuid

        mapping = ImportMapping(
            name=f"audit-mapping-{uuid.uuid4().hex[:8]}",
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

    def test_import_start_and_end_are_logged(
        self, admin_client, import_mapping, monkeypatch, caplog
    ):
        def fake_import(file, mapping):
            return {"imported": 1, "updated": 2, "skipped": 3, "errors": ["bad row"]}

        monkeypatch.setattr(ImportService, "import_with_mapping", staticmethod(fake_import))

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            resp = admin_client.post(
                "/admin/import_excel",
                data={
                    "mapping_id": str(import_mapping.id),
                    "excel_file": (io.BytesIO(b"PK\x03\x04fake"), "orders.xlsx"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        assert resp.status_code == 200
        start = [m for m in caplog.messages if m.startswith("import start")]
        end = [m for m in caplog.messages if m.startswith("import end")]
        assert start and f"mapping={import_mapping.id}" in start[0]
        assert "file=orders.xlsx" in start[0]
        assert end, "событие import end не записано"
        assert "imported=1" in end[0]
        assert "updated=2" in end[0]
        assert "skipped=3" in end[0]
        assert "errors=1" in end[0]

    def test_import_failure_is_logged(self, admin_client, import_mapping, monkeypatch, caplog):
        def fake_import(file, mapping):
            raise ValueError("битый xlsx")

        monkeypatch.setattr(ImportService, "import_with_mapping", staticmethod(fake_import))

        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            resp = admin_client.post(
                "/admin/import_excel",
                data={
                    "mapping_id": str(import_mapping.id),
                    "excel_file": (io.BytesIO(b"PK\x03\x04fake"), "orders.xlsx"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        assert resp.status_code == 200
        assert any(message.startswith("import failed") for message in caplog.messages)

    def test_rejected_file_is_not_started(self, admin_client, caplog):
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            admin_client.post(
                "/admin/import_excel",
                data={
                    "mapping_id": "0",
                    "excel_file": (io.BytesIO(b"x"), "orders.txt"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        assert not any(message.startswith("import start") for message in caplog.messages)


class TestComplaintAudit:
    def test_complaint_create_and_update_are_logged(self, db_session, sample_order, caplog):
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            ComplaintService.add(sample_order, "Интернет не работает", "new")
            ComplaintService.add(sample_order, "Интернет не работает", "closed")

        try:
            created = [m for m in caplog.messages if m.startswith("complaint create")]
            updated = [m for m in caplog.messages if m.startswith("complaint update")]
            assert created and "order=TEST-001" in created[0]
            assert "status=new" in created[0]
            assert updated and "status=closed" in updated[0]
            assert not any(
                "Интернет не работает" in m for m in caplog.messages
            ), "текст жалобы попал в лог"
        finally:
            db_session.rollback()


class TestSafeText:
    def test_safe_text_strips_newlines_and_controls(self):
        from app.utils.audit import safe_text

        forged = "ok\r\nINFO: login success role=admin\n\x00evil\t"
        cleaned = safe_text(forged)

        assert "\n" not in cleaned
        assert "\r" not in cleaned
        assert "\x00" not in cleaned
        assert cleaned == "ok INFO: login success role=admin evil"

    def test_safe_text_truncates(self):
        from app.utils.audit import safe_text

        assert safe_text("x" * 500, limit=10) == "x" * 10


class TestLoggersSurviveMigrations:
    """env.py вызывает fileConfig — он не должен выключать логгеры приложения.

    Регрессия: с `disable_existing_loggers=True` (значение по умолчанию)
    после миграций в проде умирало всё логирование, включая logs/app.log.
    """

    def test_audit_logger_is_not_disabled_after_create_app(self, app):
        audit_logger = logging.getLogger(AUDIT_LOGGER)
        assert not audit_logger.disabled, "fileConfig выключил аудит-логгер"

    def test_env_py_passes_disable_existing_loggers_false(self):
        from pathlib import Path

        env_py = Path(__file__).resolve().parents[1] / "migrations" / "env.py"
        content = env_py.read_text(encoding="utf-8")
        assert "disable_existing_loggers=False" in content
