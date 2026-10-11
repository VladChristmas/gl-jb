"""Сериализация Alembic-миграций при старте приложения.

Gunicorn и Docker поднимают несколько воркеров, и каждый вызывает
``create_app()`` -> ``flask_migrate.upgrade()``. Без сериализации воркеры
гоняются за одними и теми же ``CREATE TABLE`` / ``INSERT INTO
alembic_version``: один выигрывает, остальные падают на «already exists»
и поднимают приложение с неполной схемой.

Файловый lock (``flock`` на POSIX, ``msvcrt.locking`` на Windows) снимается
операционной системой при выходе процесса, поэтому зависших замков не бывает.
Временная папка доступна и в systemd (``PrivateTmp=true``), и в Docker, и на
Render, в отличие от каталога проекта, который может быть read-only.
"""

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from flask import Flask

LOCK_NAME = "gl_jb_migration.lock"
LOCK_TIMEOUT = 120.0
LOCK_POLL = 0.05


def _lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def migration_lock(timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    path = os.path.join(tempfile.gettempdir(), LOCK_NAME)
    handle = open(path, "a+b")
    locked = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock(handle)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Lock миграций не освобождён за {timeout:.0f} с ({path})"
                    ) from None
                time.sleep(LOCK_POLL)
        yield
    finally:
        if locked:
            try:
                _unlock(handle)
            except OSError:
                pass
        handle.close()


def run_migrations(app: Flask, directory: str | None = None) -> None:
    """Применяет миграции, пока их не выполняет ровно один процесс."""
    from flask_migrate import upgrade

    with migration_lock():
        upgrade(directory)
