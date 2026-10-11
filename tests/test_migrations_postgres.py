"""Regression: миграции должны работать на PostgreSQL, а не только на SQLite.

Baseline-ревизия исторически содержала лишь alter_column() и на чистой базе
падала с NoSuchTableError; на PostgreSQL сбой маскировался db.create_all(),
который не меняет существующие таблицы. Здесь проверяются два сценария:

1. чистая база — полная схема и alembic_version = head;
2. «легаси» база — таблицы есть, alembic_version нет (результат create_all)
   — миграции должны adopt-нуть существующую схему, а не упасть.

Тест выполняется только если PostgreSQL достижим (локальный docker-compose
или сервис CI) иначе скипается.
"""

import os
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.extensions import db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DB = "gl_jb_migrate_test"
EXPECTED_TABLES = {"users", "orders", "photos", "import_mappings", "messages", "complaints"}
EXPECTED_ORDER_COLS = {"complaint_text", "complaint_status", "complaint_date", "courier_fio"}


def _candidates() -> list[str]:
    urls = []
    if os.environ.get("TEST_PG_DSN"):
        urls.append(os.environ["TEST_PG_DSN"])
    password = dotenv_values(PROJECT_ROOT / ".env").get("POSTGRES_PASSWORD")
    if password:
        urls.append(f"postgresql://gl_jb_user:{password}@127.0.0.1:5432/{SCRATCH_DB}")
    urls.append(f"postgresql://gl_jb_user:testpass@127.0.0.1:5432/{SCRATCH_DB}")
    return urls


def _prepare(url: str) -> str | None:
    """Создаёт (если нужно) scratch-базу и возвращает DSN или None."""
    try:
        admin_url = make_url(url).set(database="postgres")
        admin = create_engine(
            admin_url,
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": 3},
        )
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": SCRATCH_DB}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
        admin.dispose()
        probe = create_engine(url, connect_args={"connect_timeout": 3})
        probe.connect().close()
        probe.dispose()
        return url
    except Exception:
        return None


@pytest.fixture(scope="module")
def pg_url():
    for url in _candidates():
        prepared = _prepare(url)
        if prepared:
            return prepared
    pytest.skip("PostgreSQL недоступен — тест миграций пропущен")


def _reset_schema(url: str) -> None:
    admin_url = make_url(url).set(database="postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    admin.dispose()


def _head_revision() -> str:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _app_for(url: str) -> Flask:
    application = Flask("migration-pg-test")
    application.config["SQLALCHEMY_DATABASE_URI"] = url
    db.init_app(application)
    Migrate(application, db)
    return application


def test_fresh_postgres_is_migrated_to_head(pg_url):
    _reset_schema(pg_url)
    application = _app_for(pg_url)

    with application.app_context():
        upgrade()

        tables = set(inspect(db.engine).get_table_names())
        assert EXPECTED_TABLES <= tables, tables

        order_cols = {col["name"]: col for col in inspect(db.engine).get_columns("orders")}
        assert EXPECTED_ORDER_COLS <= set(order_cols), order_cols

        # P1-8: токен 32 символа должен влезать в колонку
        user_cols = {col["name"]: col for col in inspect(db.engine).get_columns("users")}
        assert user_cols["token"]["type"].length == 128

        revision = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert revision == _head_revision()


def test_legacy_postgres_schema_is_adopted(pg_url):
    _reset_schema(pg_url)
    application = _app_for(pg_url)

    with application.app_context():
        db.create_all()
        db.session.execute(text("DROP TABLE IF EXISTS alembic_version"))
        db.session.commit()

        assert "alembic_version" not in inspect(db.engine).get_table_names()

        upgrade()

        tables = set(inspect(db.engine).get_table_names())
        assert EXPECTED_TABLES <= tables, tables

        revision = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert revision == _head_revision()
