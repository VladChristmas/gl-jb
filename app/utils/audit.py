"""Аудит-события (P3-4): вход в систему, импорт, изменения жалоб.

Логгер отдельный от `app.logger`, чтобы события можно было фильтровать и
в тестах (`caplog.at_level(..., logger="gl_jb.audit")`), а в проде писать
в тот же `logs/app.log` (см. `create_app`).
"""

import logging

audit_logger = logging.getLogger("gl_jb.audit")


def safe_text(value: object, limit: int = 120) -> str:
    """Однострочный фрагмент для лога: без переводов строк и управляющих
    символов (защита от log forging через имя файла или текст жалобы)."""
    text = str(value or "")
    for char in ("\r", "\n", "\t", "\x00"):
        text = text.replace(char, " ")
    text = " ".join(text.split())
    return text[:limit]


__all__ = ["audit_logger", "safe_text"]
