"""P3-1: сценарии Playwright (пользователь / админ / мобильный экран).

Поднимаем реальный HTTP-сервер поверх тестового приложения (тот же
in-memory SQLite — StaticPool, поэтому серверный поток видит тестовые данные)
и проверяем критические потоки в настоящем Chromium.

Если playwright или браузеры не установлены (например, в CI — пакета нет в
requirements-dev.txt), тесты скипаются, а не падают.
"""

import threading

import pytest

DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}

# Этап 4: ширины из брифа mobile-first (браузерная проверка scrollWidth)
VIEWPORT_WIDTHS = [320, 360, 375, 390, 412, 430, 768, 1024, 1440]


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Лимиты активны и в тестах — без сброса e2e-логины упираются в 429."""
    from app.extensions import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(scope="module")
def live_server(app):
    from werkzeug.serving import make_server

    # threaded=False: тестовая БД лежит на одном соединении (StaticPool),
    # параллельные запросы браузера не должны его переиспользовать одновременно.
    server = make_server("127.0.0.1", 0, app, threaded=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser(live_server):
    from playwright.sync_api import sync_playwright

    manager = sync_playwright().start()
    try:
        try:
            instance = manager.chromium.launch(headless=True)
        except Exception as exc:  # браузеры не скачаны — не роняем сборку
            pytest.skip(f"Chromium недоступен: {exc}")
        try:
            yield instance
        finally:
            instance.close()
    finally:
        manager.stop()


def _new_session(browser, viewport):
    """Контекст + страница + список неперехваченных JS-ошибок."""
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    return context, page, page_errors


@pytest.fixture
def photo_required_order(db_session, regular_user):
    """Заказ, который виден в /admin/orders.

    Список по умолчанию показывает только заказы с фото или ждущие фото —
    пустая таблица не проверит ни строк, ни карточного вида.
    """
    from app.models import Order

    order = Order(
        user_id=regular_user.id,
        order_number="E2E-PHOTO-1",
        description="E2E заказ",
        complaint_text="Товар побит/вскрыт",
    )
    db_session.add(order)
    db_session.commit()
    return order


def _login(page, base_url, token, expected_path):
    page.goto(f"{base_url}/login")
    page.fill("#token", token)
    page.click("button[type=submit]")
    page.wait_for_load_state()
    assert page.url.rstrip("/").endswith(expected_path), f"после входа: {page.url}"


def _assert_no_horizontal_scroll(page, label):
    has_scroll = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    assert not has_scroll, f"{label}: горизонтальный скролл на ширине {page.viewport_size['width']}"


class TestUserFlow:
    def test_login_orders_and_native_upload_form(
        self, browser, live_server, regular_user, sample_order
    ):
        context, page, page_errors = _new_session(browser, DESKTOP)
        try:
            _login(page, live_server, regular_user.token, "/dashboard")

            page.goto(f"{live_server}/orders")
            form = page.locator(f'form[action="/orders/{sample_order.id}/upload"]')
            assert form.is_visible(), "форма загрузки фото не видна"

            assert form.get_attribute("enctype") == "multipart/form-data"
            assert form.locator('input[name="photo"]').count() >= 1

            submit = form.locator('button[type="submit"]')
            assert submit.is_visible()
            assert submit.is_enabled(), "кнопка отправки заблокирована без JS (P0-1)"

            assert page_errors == [], f"JS-ошибки на странице: {page_errors}"
        finally:
            context.close()

    def test_invalid_token_shows_error(self, browser, live_server):
        context, page, _ = _new_session(browser, DESKTOP)
        try:
            page.goto(f"{live_server}/login")
            page.fill("#token", "definitely-wrong-token")
            page.click("button[type=submit]")
            page.wait_for_load_state()

            assert page.url.rstrip("/").endswith("/login")
            flashes = page.locator(".flash-error")
            assert flashes.count() >= 1, "ошибка входа не показана пользователю"
        finally:
            context.close()


class TestAdminFlow:
    def test_admin_login_shows_dashboard(self, browser, live_server):
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, DESKTOP)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")

            assert "Панель администратора" in page.content()
            assert page.locator(".kpi-card").count() >= 3
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    def test_orders_table_renders_on_desktop(self, browser, live_server, photo_required_order):
        from config import ADMIN_TOKEN

        context, page, _ = _new_session(browser, DESKTOP)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/orders")

            table = page.locator(".table-wrap table.table").first
            assert table.is_visible()
            assert table.locator("thead th").count() >= 3
            # Строки есть (иначе проверяем пустое состояние, а не данные)
            assert table.locator("tbody tr td[colspan]").count() == 0, "таблица заказов пуста"
            # На десктопе карточный вид не включается
            assert page.locator("table.is-cards").count() == 0
        finally:
            context.close()

    def test_complaints_nav_opens_complaints_page(
        self, browser, live_server, db_session, regular_user
    ):
        """Нижняя навигация «Жалобы» ведёт на /admin/complaints (этап 6)."""
        from app.models import Order
        from app.services.complaint_service import ComplaintService
        from config import ADMIN_TOKEN

        order = Order(user_id=regular_user.id, order_number="E2E-COMP-1")
        db_session.add(order)
        db_session.commit()
        ComplaintService.add(order, "Принесли чужой заказ", "открыта")
        db_session.commit()

        context, page, page_errors = _new_session(browser, MOBILE)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")

            page.locator("nav.bottom-nav a", has_text="Жалобы").click()
            page.wait_for_load_state()
            assert "/admin/complaints" in page.url, f"после клика: {page.url}"
            assert "Принесли чужой заказ" in page.content()
            # Строки ведут на заказ (§4)
            assert page.locator(f'a[href="/admin/orders/edit/{order.id}"]').count() >= 1

            _assert_no_horizontal_scroll(page, "/admin/complaints")
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()


class TestMobileFlow:
    def test_tables_switch_to_card_view_without_horizontal_scroll(
        self, browser, live_server, photo_required_order
    ):
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, MOBILE)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/orders")

            cards = page.locator("table.is-cards")
            assert cards.count() >= 1, "таблица не переключилась в карточный вид"

            labelled = page.locator("table.is-cards td[data-label]")
            assert labelled.count() >= 1, "ячейки без подписей колонок"

            has_scroll = page.evaluate(
                "document.documentElement.scrollWidth > window.innerWidth + 1"
            )
            assert not has_scroll, "страница всё ещё скроллится по горизонтали"

            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    def test_login_page_fits_mobile_viewport(self, browser, live_server):
        context, page, page_errors = _new_session(browser, MOBILE)
        try:
            page.goto(f"{live_server}/login")

            assert page.locator("#token").is_visible()
            has_scroll = page.evaluate(
                "document.documentElement.scrollWidth > window.innerWidth + 1"
            )
            assert not has_scroll, "страница входа не помещается в экран"
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    def test_chat_mobile_publish_and_bubbles(self, browser, live_server):
        """Этап 8: чат mobile-first — пузыри, тач-цели, публикация админом."""
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, MOBILE)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/chat")

            # Публикация с мобильного
            page.fill("#message-text", "Завтра смена с 8:00")
            publish = page.locator(".chat-publish-btn")
            assert publish.is_visible()
            box = publish.bounding_box()
            assert box and box["height"] >= 44, f"кнопка «Опубликовать» < 44px: {box}"
            publish.click()
            page.wait_for_load_state()

            # Сообщение в пузыре
            bubble = page.locator(".chat-bubble", has_text="Завтра смена с 8:00")
            assert bubble.count() >= 1, "сообщение не отобразилось в .chat-bubble"
            _assert_no_horizontal_scroll(page, "/chat")

            # Текстовое поле: font-size 16px против iOS-zoom (проверяем через CSS)
            textarea_size = page.evaluate(
                "getComputedStyle(document.getElementById('message-text')).fontSize"
            )
            assert textarea_size == "16px", f"textarea zoom-опасный размер: {textarea_size}"

            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    def test_courier_dashboard_pending_metric_opens_filtered_list(
        self, browser, live_server, db_session, regular_user
    ):
        """Этап 9: показатель «Ожидает фото» → список заказов с фильтром pending."""
        from app.models import Order

        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="E2E-CDASH-1",
                complaint_text="Принесли чужой заказ",
            )
        )
        db_session.commit()

        context, page, page_errors = _new_session(browser, MOBILE)
        try:
            _login(page, live_server, regular_user.token, "/dashboard")

            # На мобильных кнопки шапки скрыты (дублируют bottom nav)
            header_btn = page.locator(".dashboard-header-actions .btn").first
            if header_btn.count():
                assert not header_btn.is_visible(), "кнопки шапки видны на мобильном"

            metric = page.locator("a.kpi-card-link", has_text="Ожидает фото")
            assert metric.count() >= 1, "показатель «Ожидает фото» не ссылка"
            href = metric.first.get_attribute("href")
            assert href and "photo=pending" in href, f"href без фильтра: {href}"

            metric.first.click()
            page.wait_for_load_state()
            assert "photo=pending" in page.url, f"фильтр не применён: {page.url}"
            assert "E2E-CDASH-1" in page.content(), "ждущий фото заказ не показан"

            _assert_no_horizontal_scroll(page, "/orders?photo=pending")
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()


class TestViewportMatrix:
    """Этап 4: нет горизонтального скролла на ключевых страницах и ширинах.

    320/360/375/390/412/430 (телефоны) / 768 (планшет-граница) / 1024 / 1440
    (десктоп). Проверяются страницы главных сценариев: заказы админа,
    дашборд и заказы курьера. Если Chromium недоступен — тесты скипаются.
    """

    @pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
    def test_admin_orders_fits_width(self, browser, live_server, photo_required_order, width):
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, {"width": width, "height": 800})
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/orders")

            _assert_no_horizontal_scroll(page, "/admin/orders")
            # Контент виден (таблица или карточный вид)
            assert page.locator(".table-wrap table.table").first.is_visible()
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    @pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
    def test_courier_pages_fit_width_and_nav(
        self, browser, live_server, regular_user, sample_order, width
    ):
        context, page, page_errors = _new_session(browser, {"width": width, "height": 800})
        try:
            _login(page, live_server, regular_user.token, "/dashboard")
            _assert_no_horizontal_scroll(page, "/dashboard")

            page.goto(f"{live_server}/orders")
            _assert_no_horizontal_scroll(page, "/orders")

            # Нижняя навигация: на мобильных видна, на десктопе скрыта
            nav = page.locator("nav.bottom-nav")
            if width <= 768:
                assert nav.is_visible(), f"bottom-nav скрыта на ширине {width}"
                # Тач-цели пунктов >= 44px
                box = nav.locator(".bottom-nav-item").first.bounding_box()
                assert box and box["height"] >= 44, f"таргет меньше 44px: {box}"

                # Этап 7: кнопки загрузки фото — тач-цели >= 44px
                upload_btn = page.locator(".order-actions .upload-btn").first
                if upload_btn.count():
                    btn_box = upload_btn.bounding_box()
                    assert btn_box and btn_box["height"] >= 44, f"кнопка загрузки < 44px: {btn_box}"
                # Чип-фильтр «Ждут фото» виден и кликабелен
                chip = page.locator(".order-filter-chips a", has_text="Ждут фото")
                assert chip.first.is_visible(), "чип «Ждут фото» не виден"
            else:
                assert not nav.is_visible(), f"bottom-nav видна на десктопе {width}"

            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    @pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
    def test_admin_dashboard_fits_width(self, browser, live_server, width):
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, {"width": width, "height": 800})
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            _assert_no_horizontal_scroll(page, "/admin")

            nav = page.locator("nav.bottom-nav")
            if width <= 768:
                assert nav.is_visible()
                # Быстрые действия скрыты на мобильных (дублируют bottom nav)
                assert not page.locator(".quick-actions").is_visible()
            else:
                assert not nav.is_visible()
                assert page.locator(".quick-actions").is_visible()

            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    def test_dashboard_attention_card_opens_filtered_list(
        self, browser, live_server, db_session, regular_user
    ):
        """§4: карточка «Ожидают фото» ведёт на список с фильтром without_photos."""
        from app.models import Order
        from config import ADMIN_TOKEN

        db_session.add(
            Order(
                user_id=regular_user.id,
                order_number="E2E-ATTN-1",
                description="для карточки проблем",
                complaint_text="Принесли чужой заказ",
            )
        )
        db_session.commit()

        context, page, page_errors = _new_session(browser, MOBILE)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/")

            # Новый шаблон: attention-item вместо attention-card
            card = page.locator(".attention-item").first
            assert card.is_visible(), "карточка «Требует внимания» не видна"
            href = card.get_attribute("href")
            assert href and "photo_filter=without_photos" in href, f"href без фильтра: {href}"

            card.click()
            page.wait_for_load_state()
            assert "photo_filter=without_photos" in page.url, f"фильтр не применён: {page.url}"

            _assert_no_horizontal_scroll(page, "/admin/orders?photo_filter=without_photos")
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()

    @pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
    def test_stage_6_to_11_pages_fit_width(
        self, browser, live_server, db_session, regular_user, width
    ):
        """Этап 12: новые страницы этапов 6–11 входят в viewport-матрицу.

        /admin/complaints, /chat, /orders?photo=pending, /orders/<id>/photos.
        """
        # Данные: заказ с фото-подтемой (ждущий фото) + жалоба
        from app.models import Order
        from app.services.complaint_service import ComplaintService
        from config import ADMIN_TOKEN

        order = Order(
            user_id=regular_user.id,
            order_number=f"VP-{width}",
            address=f"Адрес {width}",
            complaint_text="Принесли чужой заказ",
        )
        db_session.add(order)
        db_session.commit()
        ComplaintService.add(order, "Принесли чужой заказ", "открыта")
        db_session.commit()

        context, page, page_errors = _new_session(browser, {"width": width, "height": 800})
        try:
            # Админ: жалобы
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/complaints")
            _assert_no_horizontal_scroll(page, "/admin/complaints")

            # Курьер: pending-фильтр и галерея фото
            page.goto(f"{live_server}/login")
            page.fill("#token", regular_user.token)
            page.click("button[type=submit]")
            page.wait_for_load_state()

            page.goto(f"{live_server}/orders?photo=pending")
            _assert_no_horizontal_scroll(page, "/orders?photo=pending")

            page.goto(f"{live_server}/orders/{order.id}/photos")
            _assert_no_horizontal_scroll(page, f"/orders/{order.id}/photos")

            # Чат (курьер — читает)
            page.goto(f"{live_server}/chat")
            _assert_no_horizontal_scroll(page, "/chat")

            assert page_errors == [], f"JS-ошибки на ширине {width}: {page_errors}"
        finally:
            context.close()

    @pytest.mark.parametrize("width", VIEWPORT_WIDTHS)
    def test_admin_photos_fits_width(self, browser, live_server, width):
        """Viewport matrix для /admin/photos (контроль фотографий)."""
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, {"width": width, "height": 800})
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/photos")
            _assert_no_horizontal_scroll(page, "/admin/photos")

            nav = page.locator("nav.bottom-nav")
            if width <= 768:
                assert nav.is_visible(), f"bottom-nav не видна на {width}px"
            else:
                assert not nav.is_visible(), f"bottom-nav не должна быть видна на {width}px"

            assert page_errors == [], f"JS-ошибки на ширине {width}: {page_errors}"
        finally:
            context.close()


class TestJsRegressions:
    """P2-фиксы: confirmDelete как глобальная функция и живой счётчик pending."""

    def test_confirm_delete_shows_undo_toast_before_submitting(
        self, browser, live_server, photo_required_order
    ):
        """Удаление идёт через undo-тост, а не сразу.

        До фикса confirmDelete был только GLB.confirmDelete, шаблоны вызывали
        confirmDelete(...) → ReferenceError → форма удалялась без подтверждения.
        """
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, DESKTOP)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            page.goto(f"{live_server}/admin/orders")

            action = f"/admin/orders/delete/{photo_required_order.id}"
            form = page.locator(f'form[action="{action}"]')
            assert form.count() == 1, "Форма удаления заказа не найдена"

            url_before = page.url
            form.locator('button[type="submit"]').click()
            page.wait_for_timeout(400)

            assert (
                page.url == url_before
            ), "Заказ удалился без подтверждения (confirmDelete не сработал)"
            toast = page.locator(".toast")
            assert toast.count() >= 1, "Undo-тост не показан"
            assert f"Удалить заказ #{photo_required_order.id}" in toast.inner_text()
            assert page_errors == [], f"JS-ошибки: {page_errors}"

            # Подтверждаем удаление кликом по кнопке тоста
            toast.locator("button.toast-action").click()
            page.wait_for_load_state()
            page.wait_for_timeout(400)
            assert (
                page.locator(f'form[action="{action}"]').count() == 0
            ), "Заказ не удалился после подтверждения в тосте"
            assert page_errors == [], f"JS-ошибки после подтверждения: {page_errors}"
        finally:
            context.close()

    def test_pending_count_badge_refreshes_via_js(self, browser, live_server):
        """P2: второй аргумент initNotifications(apiUrl, pendingUrl) подключён."""
        from config import ADMIN_TOKEN

        context, page, page_errors = _new_session(browser, DESKTOP)
        try:
            _login(page, live_server, ADMIN_TOKEN, "/admin")
            assert page.locator("#pending-count").count() == 1

            arity = page.evaluate("GLB.initNotifications.length")
            assert (
                arity == 2
            ), f"initNotifications игнорирует второй аргумент (arity={arity}, ждём 2)"

            page.evaluate("document.getElementById('pending-count').textContent = 'STALE'")
            page.evaluate(
                "GLB.initNotifications(" "'/admin/api/notifications', '/admin/api/pending_count')"
            )
            page.wait_for_function(
                "document.getElementById('pending-count').textContent !== 'STALE'",
                timeout=5000,
            )
            text = page.locator("#pending-count").inner_text()
            assert text.strip().isdigit(), f"Бейдж показывает не число: {text!r}"
            assert page_errors == [], f"JS-ошибки: {page_errors}"
        finally:
            context.close()
