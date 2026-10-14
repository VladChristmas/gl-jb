"""Regression: РјРёРіСЂР°С†РёРё РґРѕР»Р¶РЅС‹ СЂР°Р±РѕС‚Р°С‚СЊ РЅР° С‡РёСЃС‚РѕР№ Рё РЅР° В«Р»РµРіР°СЃРёВ» Р‘Р”.

Р Р°РЅСЊС€Рµ baseline-СЂРµРІРёР·РёСЏ СЃРѕРґРµСЂР¶Р°Р»Р° С‚РѕР»СЊРєРѕ alter_column() Рё РїР°РґР°Р»Р° СЃ
NoSuchTableError вЂ” РїСЂРёР»РѕР¶РµРЅРёРµ РІС‹Р¶РёРІР°Р»Рѕ Р·Р° СЃС‡С‘С‚ Р·Р°РїСЂРµС‰С‘РЅРЅРѕРіРѕ db.create_all()
fallback Рё СЂСѓС‡РЅС‹С… ALTER TABLE РїСЂРё СЃС‚Р°СЂС‚Рµ. РќР° PostgreSQL СЌС‚Рѕ РјРѕР»С‡Р° СЂР°Р·СЉРµР·Р¶Р°Р»РѕСЃСЊ.
"""

from flask import Flask
from flask_migrate import Migrate, upgrade
from sqlalchemy import inspect, text

from app.extensions import db

EXPECTED_TABLES = {"users", "orders", "photos", "import_mappings", "messages", "complaints"}
EXPECTED_ORDER_COLS = {
    "complaint_text",
    "complaint_status",
    "complaint_date",
    "external_id",
    "courier_fio",
    "address",
    "delivered_at",
    "picker_fio",
    "pick_count",
    "wait_time",
    "pick_speed",
}


def _app_for(uri: str) -> Flask:
    application = Flask("migration-test")
    application.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(application)
    Migrate(application, db)
    return application


def _columns(table: str) -> set[str]:
    return {col["name"] for col in inspect(db.engine).get_columns(table)}


def test_fresh_database_is_migrated(tmp_path):
    uri = "sqlite:///" + (tmp_path / "fresh.db").as_posix()
    application = _app_for(uri)

    with application.app_context():
        upgrade()

        tables = set(inspect(db.engine).get_table_names())
        assert EXPECTED_TABLES <= tables, f"РЅРµС‚ С‚Р°Р±Р»РёС†: {EXPECTED_TABLES - tables}"
        assert EXPECTED_ORDER_COLS <= _columns("orders")
        assert "role" in _columns("users")

        version = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version, "alembic_version РґРѕР»Р¶РЅР° Р±С‹С‚СЊ РїСЂРѕС€С‚Р°РјРїРѕРІР°РЅР°"


def test_fresh_migration_is_idempotent(tmp_path):
    uri = "sqlite:///" + (tmp_path / "twice.db").as_posix()
    application = _app_for(uri)

    with application.app_context():
        upgrade()
        first = set(inspect(db.engine).get_table_names())
        upgrade()  # РїРѕРІС‚РѕСЂРЅС‹Р№ СЃС‚Р°СЂС‚ РїСЂРёР»РѕР¶РµРЅРёСЏ
        assert set(inspect(db.engine).get_table_names()) == first


def test_legacy_create_all_database_is_adopted(tmp_path):
    """Р‘Р”, СЃРѕР·РґР°РЅРЅР°СЏ db.create_all() Р±РµР· alembic_version (С‚РµРєСѓС‰РµРµ РїСЂРѕРґ-СЃРѕСЃС‚РѕСЏРЅРёРµ)."""
    import app.models  # noqa: F401  вЂ” СЂРµРіРёСЃС‚СЂРёСЂСѓРµС‚ РјРѕРґРµР»Рё

    uri = "sqlite:///" + (tmp_path / "legacy.db").as_posix()

    setup = Flask("legacy-setup")
    setup.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(setup)
    Migrate(setup, db)
    with setup.app_context():
        db.create_all()  # РєР°Рє РЅР° РїСЂРѕРґРµ РґРѕ РёСЃРїСЂР°РІР»РµРЅРёСЏ РјРёРіСЂР°С†РёР№
        assert "alembic_version" not in inspect(db.engine).get_table_names()

    application = _app_for(uri)
    with application.app_context():
        upgrade()

        version = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert (
            version
        ), "Р»РµРіР°СЃРё-Р±Р°Р·Р° РґРѕР»Р¶РЅР° Р±С‹С‚СЊ РїСЂРѕС€С‚Р°РјРїРѕРІР°РЅР° РїРѕСЃР»Рµ upgrade"
        assert EXPECTED_ORDER_COLS <= _columns("orders")
        assert "role" in _columns("users")


def test_backfill_complaints_created_at(tmp_path):
    """c7b6a9f8e2d1: NULL created_at жалоб получает дату заказа, не трогая заполненные.

    Приоритет: orders.complaint_date (реальная дата обращения), иначе
    orders.created_at (дата импорта). Строка с уже известной датой — не меняется.
    """
    uri = "sqlite:///" + (tmp_path / "backfill.db").as_posix()
    application = _app_for(uri)

    with application.app_context():
        # Схема как на проде до бэкоффила (таблица complaints уже есть)
        upgrade(revision="add_operational_analysis")

        db.session.execute(text("INSERT INTO users (id, fio, token) VALUES (1, 'Тест', 'tok1')"))
        # Заказ без даты жалобы в зеркале — возьмём created_at заказа
        db.session.execute(
            text(
                "INSERT INTO orders (id, user_id, created_at) "
                "VALUES (1, 1, '2026-10-06 16:54:29')"
            )
        )
        # Заказ с датой жалобы в зеркале — приоритет у неё
        db.session.execute(
            text(
                "INSERT INTO orders (id, user_id, created_at, complaint_date) "
                "VALUES (2, 1, '2026-10-06 16:54:29', '2026-09-01 10:00:00')"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO complaints (id, order_id, text, created_at) VALUES "
                "(1, 1, 'жалоба без даты', NULL), "
                "(2, 2, 'жалоба без даты, дата есть в зеркале', NULL), "
                "(3, 2, 'жалоба с известной датой', '2026-09-02 12:00:00')"
            )
        )
        db.session.commit()

        upgrade()  # head — прогоняет backfill

        rows = dict(
            db.session.execute(text("SELECT id, created_at FROM complaints ORDER BY id")).fetchall()
        )
        assert rows[1] == "2026-10-06 16:54:29", "нет created_at заказа — нет и даты жалобы"
        assert rows[2] == "2026-09-01 10:00:00", "приоритет у complaint_date из зеркала"
        assert rows[3] == "2026-09-02 12:00:00", "известная дата не должна меняться"


def test_create_app_runs_migrations_without_create_all(tmp_path, monkeypatch):
    """create_app() РѕР±СЏР·Р°РЅ РїРѕР»СѓС‡РёС‚СЊ СЃС…РµРјСѓ РёР· Alembic, Р° РЅРµ РёР· db.create_all()."""
    import app as app_pkg

    uri = "sqlite:///" + (tmp_path / "app.db").as_posix()
    monkeypatch.setattr(app_pkg, "DATABASE_URL", uri)

    calls = []
    monkeypatch.setattr(type(db), "create_all", lambda self, *a, **k: calls.append(1))

    application = app_pkg.create_app()
    application.config["TESTING"] = True

    with application.app_context():
        tables = set(inspect(db.engine).get_table_names())
        assert EXPECTED_TABLES <= tables
        assert "role" in _columns("users")

    assert not calls, "create_app() РЅРµ РґРѕР»Р¶РµРЅ РІС‹Р·С‹РІР°С‚СЊ db.create_all()"
