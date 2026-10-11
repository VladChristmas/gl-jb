"""Восстановление фото с ПК: POST /admin/api/photos/restore (scripts/push_photos.ps1).

ПК — источник истины: скрипт сравнивает локальные файлы со списком
/admin/api/files и заливает недостающие. Проверяем auth, защиту от path
traversal, идемпотентность, конфликты размеров, CSRF-исключение и лимит.
"""

import base64
import importlib
import logging

import pytest

from app.extensions import limiter
from config import ADMIN_TOKEN

photo_service_module = importlib.import_module("app.services.photo_service")
admin_module = importlib.import_module("app.blueprints.admin")

AUDIT_LOGGER = "gl_jb.audit"
RESTORE_URL = "/admin/api/photos/restore"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"photo-bytes"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def restore_folder(tmp_path, monkeypatch):
    """Пишем только во временную папку, а не в реальные uploads/."""
    folder = tmp_path / "uploads"
    folder.mkdir()
    monkeypatch.setattr(photo_service_module, "UPLOAD_FOLDER", str(folder))
    monkeypatch.setattr(admin_module, "UPLOAD_FOLDER", str(folder))
    return folder


def make_item(name: str, raw: bytes = PNG_BYTES) -> dict:
    return {
        "name": name,
        "data": base64.b64encode(raw).decode("ascii"),
        "_raw": raw,
    }


def post_restore(client, items, overwrite=False, token=ADMIN_TOKEN, **kwargs):
    body = {
        "files": [{"name": i["name"], "data": i["data"]} for i in items],
        "overwrite": overwrite,
    }
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers.setdefault("X-Admin-Token", token)
    return client.post(RESTORE_URL, json=body, headers=headers, **kwargs)


class TestRestoreAuth:
    def test_requires_token(self, client):
        resp = post_restore(client, [make_item("1_1_aaaaaaaa.jpg")], token=None)
        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        resp = post_restore(client, [make_item("1_1_aaaaaaaa.jpg")], token="WRONG-TOKEN")
        assert resp.status_code == 401

    def test_admin_session_is_accepted(self, admin_client):
        resp = post_restore(client=admin_client, items=[make_item("1_1_ssssssss.jpg")], token=None)
        assert resp.status_code == 200
        assert resp.get_json()["uploaded"] == 1


class TestRestoreFiles:
    def test_writes_file_with_exact_bytes(self, client, restore_folder):
        item = make_item("1_2_ab12cd34.jpg")
        resp = post_restore(client, [item])

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["uploaded"] == 1
        assert data["bytes"] == len(PNG_BYTES)
        assert data["failed"] == []
        assert (restore_folder / "1_2_ab12cd34.jpg").read_bytes() == PNG_BYTES

    def test_is_idempotent_on_repeat(self, client, restore_folder):
        item = make_item("1_2_ab12cd34.jpg")
        post_restore(client, [item])
        resp = post_restore(client, [item])

        data = resp.get_json()
        assert data["uploaded"] == 0
        assert data["skipped"] == 1
        assert data["bytes"] == 0
        assert (restore_folder / "1_2_ab12cd34.jpg").read_bytes() == PNG_BYTES

    def test_uploads_only_missing_from_server(self, client, restore_folder):
        post_restore(client, [make_item("1_2_present.jpg")])
        resp = post_restore(
            client,
            [make_item("1_2_present.jpg"), make_item("1_3_missing.jpg", b"other-bytes")],
        )

        data = resp.get_json()
        assert data["uploaded"] == 1
        assert data["skipped"] == 1
        assert (restore_folder / "1_3_missing.jpg").read_bytes() == b"other-bytes"

    def test_conflict_needs_explicit_overwrite(self, client, restore_folder):
        post_restore(client, [make_item("1_4_conflict.jpg", b"AAAA")])

        resp = post_restore(client, [make_item("1_4_conflict.jpg", b"BBBBBB")])
        data = resp.get_json()
        assert data["conflicts"] and data["failed"] == []
        assert (restore_folder / "1_4_conflict.jpg").read_bytes() == b"AAAA"

        resp = post_restore(client, [make_item("1_4_conflict.jpg", b"BBBBBB")], overwrite=True)
        data = resp.get_json()
        assert data["uploaded"] == 1
        assert data["conflicts"] == []
        assert (restore_folder / "1_4_conflict.jpg").read_bytes() == b"BBBBBB"

    def test_files_appear_in_admin_file_list(self, client, restore_folder):
        post_restore(client, [make_item("1_5_visible.jpg")])

        resp = client.get("/admin/api/files", headers={"X-Admin-Token": ADMIN_TOKEN})
        names = [f["name"] for f in resp.get_json()["files"]]
        assert "1_5_visible.jpg" in names


class TestRestoreValidation:
    @pytest.mark.parametrize("bad_name", ["../evil.jpg", "..\\evil.jpg", "sub/dir.jpg"])
    def test_path_traversal_is_rejected(self, client, restore_folder, bad_name):
        resp = post_restore(client, [make_item(bad_name)])

        data = resp.get_json()
        assert data["uploaded"] == 0
        assert data["failed"] and data["failed"][0]["reason"]
        assert not (restore_folder.parent / "evil.jpg").exists()
        assert list(restore_folder.iterdir()) == []

    @pytest.mark.parametrize("bad_name", ["evil.exe", "no-extension", "archive.jpg.exe"])
    def test_bad_extension_is_rejected(self, client, restore_folder, bad_name):
        resp = post_restore(client, [make_item(bad_name)])

        data = resp.get_json()
        assert data["uploaded"] == 0
        assert data["failed"] and "формат" in data["failed"][0]["reason"]

    def test_invalid_base64_is_reported(self, client, restore_folder):
        resp = post_restore(client, [{"name": "1_6_bad.jpg", "data": "@@@not-base64@@@"}])

        data = resp.get_json()
        assert data["uploaded"] == 0
        assert data["failed"][0]["reason"] == "некорректный base64"
        assert list(restore_folder.iterdir()) == []

    def test_empty_file_is_rejected(self, client, restore_folder):
        resp = post_restore(client, [make_item("1_7_empty.jpg", b"")])

        data = resp.get_json()
        assert data["failed"][0]["reason"] == "пустой файл"
        assert list(restore_folder.iterdir()) == []

    @pytest.mark.parametrize(
        "payload",
        [{}, {"files": []}, {"files": "nope"}, "just-a-string"],
    )
    def test_bad_body_is_400(self, client, payload):
        resp = client.post(RESTORE_URL, json=payload, headers={"X-Admin-Token": ADMIN_TOKEN})
        assert resp.status_code == 400

    def test_item_without_name_is_reported_not_fatal(self, client, restore_folder):
        resp = client.post(
            RESTORE_URL,
            json={"files": [{"data": "AAAA"}]},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["uploaded"] == 0
        assert data["failed"][0]["reason"] == "пустое имя файла"

    def test_malformed_json_is_400(self, client):
        resp = client.post(
            RESTORE_URL,
            data="{not json",
            content_type="application/json",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 400


class TestRestoreTransport:
    def test_endpoint_is_csrf_exempt(self, app, client, restore_folder):
        """В проде CSRF включён: запрос без токена формы должен проходить."""
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            resp = post_restore(client, [make_item("1_8_csrf.jpg")])
            assert resp.status_code == 200
            assert resp.get_json()["uploaded"] == 1

            # контроль: форма без csrf_token должна отбрасываться
            control = client.post("/admin/login", data={"password": "x"})
            assert control.status_code == 400
        finally:
            app.config["WTF_CSRF_ENABLED"] = False

    def test_oversized_body_returns_413(self, app, client, restore_folder):
        original = app.config["MAX_CONTENT_LENGTH"]
        app.config["MAX_CONTENT_LENGTH"] = 64
        try:
            resp = post_restore(
                client,
                [make_item("1_9_big.jpg", b"x" * 512)],
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 413
            assert "error" in resp.get_json()
        finally:
            app.config["MAX_CONTENT_LENGTH"] = original

    def test_rate_limited_to_60_per_minute(self, client, restore_folder):
        item = make_item("1_10_rate.jpg")
        status = None
        for _ in range(61):
            status = post_restore(client, [item]).status_code
        assert status == 429


class TestRestoreAudit:
    def test_restore_is_logged_without_file_contents(self, client, caplog):
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
            post_restore(client, [make_item("1_11_logged.jpg")])

        joined = "\n".join(caplog.messages)
        assert "photo restore total=1 uploaded=1" in joined
        assert "1_11_logged.jpg" not in joined, "имя файла не должно попадать в лог"
