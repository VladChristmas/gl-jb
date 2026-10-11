"""Regression: параллельный старт воркеров не должен ронять миграции.

Gunicorn и Docker поднимают несколько воркеров, каждый вызывает
``create_app()`` -> ``run_migrations()``. Без блокировки воркеры гоняются за
одними и теми же ``CREATE TABLE`` / ``INSERT INTO alembic_version``:
проигравший падает, а приложение поднимается с неполной схемой.
"""

import os
import subprocess
import sys
import time

from sqlalchemy import create_engine, inspect

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_TABLES = {"users", "orders", "photos", "import_mappings", "messages"}

HOLDER_SCRIPT = """
import time
from app.utils.migration_lock import migration_lock

with migration_lock(timeout=30):
    print("locked", flush=True)
    time.sleep(1.5)
print("released", flush=True)
"""

START_SCRIPT = """
from app import create_app

create_app()
print("started", flush=True)
"""


def _env(database_url: str) -> dict:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = PROJECT_ROOT
    env["FLASK_DEBUG"] = "1"
    return env


def _run(code: str, env: dict, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_migration_lock_blocks_until_holder_releases():
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER_SCRIPT],
        cwd=PROJECT_ROOT,
        env=_env("sqlite:///:memory:"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        acquired = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line.strip() == "locked":
                acquired = True
                break
        assert acquired, f"holder не смог захватить lock: {proc.communicate(timeout=10)[0]}"

        from app.utils.migration_lock import migration_lock

        started = time.monotonic()
        with migration_lock(timeout=30):
            waited = time.monotonic() - started
        assert waited >= 0.8, f"lock отпущен мгновенно ({waited:.2f} с) — блокировки нет"
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_concurrent_workers_all_start_with_full_schema(tmp_path):
    uri = "sqlite:///" + (tmp_path / "shared.db").as_posix()
    env = _env(uri)

    codes = [START_SCRIPT] * 3
    results = [_run(code, env) for code in codes]

    for returncode, output in results:
        assert returncode == 0, output

    tables = set(inspect(create_engine(uri)).get_table_names())
    assert EXPECTED_TABLES <= tables, tables
    assert "alembic_version" in tables, tables
