"""Regression: фото должно загружаться без JavaScript (P0-1).

Раньше кнопка «Загрузить фото» была `<button type="button" disabled>` и
включалась только из JS, а сама отправка шла через fetch(). При отключённом
или упавшем JS, в старом браузере и в offline-PWA выбрать фото было нельзя.
Теперь это нативная `<form enctype="multipart/form-data">` с двумя input
(камера `capture=environment` и галерея), JS отвечает только за превью.
"""

import io
import re

from app.services.photo_service import PhotoService

JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01])


def test_orders_page_renders_native_multipart_form(auth_client, sample_order):
    resp = auth_client.get("/orders")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert f'action="/orders/{sample_order.id}/upload"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'capture="environment"' in html
    assert 'name="photo"' in html
    assert html.count(f'action="/orders/{sample_order.id}/upload"') == 1

    submit = re.search(r'<button[^>]*type="submit"[^>]*>', html)
    assert submit is not None, "кнопка отправки формы отсутствует"
    assert "disabled" not in submit.group(0), "submit заблокирован без JS"


def test_native_form_upload_without_javascript(auth_client, sample_order, regular_user):
    resp = auth_client.post(
        f"/orders/{sample_order.id}/upload",
        data={"photo": (io.BytesIO(JPEG + b"no-js upload"), "photo.jpg")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/orders")

    photos = PhotoService.get_order_photos(sample_order.id)
    assert len(photos) == 1
    try:
        assert photos[0].original_filename == "photo.jpg"
    finally:
        PhotoService.delete_photo_admin(photos[0].id, sample_order.id)


def test_unused_camera_input_with_empty_filename_is_skipped(
    auth_client, sample_order, regular_user
):
    """Браузер отправляет оба input name='photo', у неиспользованного пустой filename."""
    resp = auth_client.post(
        f"/orders/{sample_order.id}/upload",
        data={
            "photo": [
                (io.BytesIO(b""), ""),
                (io.BytesIO(JPEG + b"second input"), "second.jpg"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 302

    photos = PhotoService.get_order_photos(sample_order.id)
    assert len(photos) == 1
    try:
        assert photos[0].original_filename == "second.jpg"
    finally:
        PhotoService.delete_photo_admin(photos[0].id, sample_order.id)


def test_upload_without_any_file_flashes_error(auth_client, sample_order):
    resp = auth_client.post(
        f"/orders/{sample_order.id}/upload",
        data={},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/orders")
    assert PhotoService.get_order_photos(sample_order.id) == []


def test_upload_error_states_format_reason(auth_client, sample_order):
    """P2-10: пользователь видит причину отказа, а не «ошибка загрузки»."""
    resp = auth_client.post(
        f"/orders/{sample_order.id}/upload",
        data={"photo": (io.BytesIO(b"plain text"), "note.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    html = resp.get_data(as_text=True)
    assert "Ошибка загрузки фото:" in html
    assert "Недопустимый формат" in html
    assert PhotoService.get_order_photos(sample_order.id) == []


def test_upload_error_states_size_reason(auth_client, sample_order, monkeypatch):
    import app.services.photo_service as photo_service_module

    monkeypatch.setattr(photo_service_module, "MAX_FILE_SIZE", 8)

    resp = auth_client.post(
        f"/orders/{sample_order.id}/upload",
        data={"photo": (io.BytesIO(JPEG + b"x" * 64), "big.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    html = resp.get_data(as_text=True)
    assert "Ошибка загрузки фото:" in html
    assert "слишком большой" in html.lower()
    assert PhotoService.get_order_photos(sample_order.id) == []
