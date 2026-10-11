"""Regression: ошибки обязаны отвечать JSON AJAX/fetch-клиентам (P1-10, P1-13).

Раньше все errorhandler'ы отдавали HTML: JSON-клиент получал разметку вместо
`{"ok": false}`, а `is_ajax` определялся только по `X-CSRFToken` — fetch-запрос
без него уходил в redirect.
"""

from flask import abort, render_template

import app as app_pkg

JSON_HINTS = [
    {"X-Requested-With": "XMLHttpRequest"},
    {"X-CSRFToken": "any-token"},
    {"Accept": "application/json"},
]


def _app_with_error_routes():
    application = app_pkg.create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)

    @application.route("/__err401")
    def _err401():
        abort(401)

    @application.route("/__err403")
    def _err403():
        abort(403)

    return application


class TestErrorJsonForAjax:
    def test_404_is_json_for_any_json_hint(self):
        application = _app_with_error_routes()
        client = application.test_client()

        for headers in JSON_HINTS:
            resp = client.get("/definitely-missing-page", headers=headers)
            assert resp.status_code == 404
            data = resp.get_json()
            assert data["ok"] is False, f"заголовки {headers}: {data}"
            assert data["error"] == "Страница не найдена"

    def test_404_is_html_for_browser(self):
        application = _app_with_error_routes()
        client = application.test_client()

        resp = client.get("/definitely-missing-page")
        assert resp.status_code == 404
        assert resp.get_json() is None
        assert b"404" in resp.data

    def test_401_is_json_for_ajax_and_html_for_browser(self):
        application = _app_with_error_routes()
        client = application.test_client()

        resp = client.get("/__err401", headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 401
        assert resp.get_json() == {"ok": False, "error": "Требуется вход"}

        resp = client.get("/__err401")
        assert resp.status_code == 401
        assert resp.get_json() is None
        assert "401" in resp.get_data(as_text=True)

    def test_403_is_json_for_ajax_and_html_for_browser(self):
        application = _app_with_error_routes()
        client = application.test_client()

        resp = client.get("/__err403", headers={"Accept": "application/json"})
        assert resp.status_code == 403
        assert resp.get_json() == {"ok": False, "error": "Доступ запрещён"}

        resp = client.get("/__err403")
        assert resp.status_code == 403
        assert resp.get_json() is None
        assert "403" in resp.get_data(as_text=True)


class TestErrorTemplates:
    def test_401_and_403_templates_render(self, app):
        with app.test_request_context():
            html_401 = render_template("errors/401.html")
            html_403 = render_template("errors/403.html")
        assert "401" in html_401
        assert "403" in html_403


class TestUploadIsAjaxDetection:
    """P1-13: is_ajax не должен зависеть только от X-CSRFToken."""

    def test_x_requested_with_gets_json(self, auth_client, sample_order):
        resp = auth_client.post(
            f"/orders/{sample_order.id}/upload",
            data={},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_accept_json_gets_json(self, auth_client, sample_order):
        resp = auth_client.post(
            f"/orders/{sample_order.id}/upload",
            data={},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_plain_form_post_still_redirects(self, auth_client, sample_order):
        resp = auth_client.post(f"/orders/{sample_order.id}/upload", data={})
        assert resp.status_code == 302
