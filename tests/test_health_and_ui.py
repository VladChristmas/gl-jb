from flask import render_template


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data['database'] == 'ok'


class TestImportPages:
    def test_import_excel_has_back_button_and_csrf(self, admin_client):
        resp = admin_client.get('/admin/import_excel')
        assert resp.status_code == 200
        assert b'btn-back' in resp.data
        assert b'name="csrf_token"' in resp.data

    def test_import_mappings_has_back_button_and_csrf(self, admin_client):
        resp = admin_client.get('/admin/import_mappings')
        assert resp.status_code == 200
        assert b'btn-back' in resp.data
        assert b'name="csrf_token"' in resp.data


class TestThemeSwitcher:
    def test_login_page_has_theme_control(self, client):
        resp = client.get('/login')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'data-theme-value="light"' in html
        assert 'data-theme-value="dark"' in html
        assert 'data-theme-value="system"' in html
        assert "localStorage.getItem('theme')" in html

    def test_admin_dashboard_has_theme_control(self, admin_client):
        html = admin_client.get('/admin/').get_data(as_text=True)
        assert 'class="theme-switch"' in html
        assert 'class="topbar"' in html


class TestErrorPages:
    def test_404_page(self, client):
        resp = client.get('/definitely-missing-page')
        assert resp.status_code == 404

    def test_400_template_renders_with_hint(self, app):
        with app.test_request_context():
            html = render_template('errors/400.html', hint='Обновите страницу')
        assert 'Обновите страницу' in html

    def test_413_template_renders(self, app):
        with app.test_request_context():
            html = render_template('errors/413.html')
        assert '413' in html

    def test_429_template_renders(self, app):
        with app.test_request_context():
            html = render_template('errors/429.html')
        assert '429' in html
