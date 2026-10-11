"""Распознавание AJAX/JSON-клиентов (P1-10, P1-13)."""

from flask import Request


def wants_json(request: Request) -> bool:
    """True, если клиент ждёт JSON, а не HTML-страницу или редирект.

    Проверяем три признака, потому что `fetch()` не шлёт `X-Requested-With`:
    XHR-заголовок, `X-CSRFToken` (фронт подставляет его в своих запросах)
    и `Accept: application/json`.
    """
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    if request.headers.get("X-CSRFToken"):
        return True
    return "application/json" in (request.headers.get("Accept") or "")
