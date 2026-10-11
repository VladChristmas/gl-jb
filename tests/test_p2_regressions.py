"""Regression: правки уровня P2, которые легко откатить.

- индексы FK и unique (user_id, order_number) / users.token — уровень БД;
- счётчики фото одним запросом вместо N+1;
- размер uploads/ кэшируется, а не считается os.walk на каждый запрос.
"""

import time
from pathlib import Path

from sqlalchemy import inspect

from app.blueprints.admin import _UPLOAD_SIZE_CACHE, _UPLOAD_SIZE_TTL, _upload_size
from app.extensions import db
from app.models import Order, User
from app.services.photo_service import PhotoService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestDatabaseIndexes:
    def test_fresh_schema_has_p2_indexes(self, app):
        with app.app_context():
            inspector = inspect(db.engine)

            photos_indexes = {i["name"]: i for i in inspector.get_indexes("photos")}
            assert "ix_photos_order_id" in photos_indexes
            assert "ix_photos_filename" in photos_indexes

            orders_indexes = {i["name"]: i for i in inspector.get_indexes("orders")}
            assert "ix_orders_user_id" in orders_indexes

    def test_orders_unique_user_and_number(self, app):
        with app.app_context():
            inspector = inspect(db.engine)
            unique = {
                tuple(c.get("column_names") or ())
                for c in inspector.get_unique_constraints("orders")
            }
            unique |= {
                tuple(i.get("column_names") or ())
                for i in inspector.get_indexes("orders")
                if i.get("unique")
            }
            assert (
                "user_id",
                "order_number",
            ) in unique, "нет unique (user_id, order_number) — импорт может создать дубль"

    def test_users_token_unique(self, app):
        with app.app_context():
            inspector = inspect(db.engine)
            unique = {
                tuple(c.get("column_names") or ())
                for c in inspector.get_unique_constraints("users")
            }
            unique |= {
                tuple(i.get("column_names") or ())
                for i in inspector.get_indexes("users")
                if i.get("unique")
            }
            assert ("token",) in unique

    def test_duplicate_order_number_blocks_unique_index(self, tmp_path):
        """Если дубли уже есть — миграция не должна ронять старт приложения."""
        import logging
        import logging.config
        from unittest import mock

        from alembic import command
        from alembic.config import Config as AlembicConfig
        from flask import Flask
        from flask_migrate import Migrate

        uri = "sqlite:///" + (tmp_path / "dupes.db").as_posix()
        application = Flask("dup-migration-test")
        application.config["SQLALCHEMY_DATABASE_URI"] = uri
        db.init_app(application)
        Migrate(application, db)

        cfg = AlembicConfig(str(PROJECT_ROOT / "migrations" / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

        warnings: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                warnings.append(record.getMessage())

        capture = _Capture()
        real_file_config = logging.config.fileConfig

        def _file_config(*args, **kwargs):
            result = real_file_config(*args, **kwargs)
            # fileConfig вычищает handlers существующих логгеров — ставим
            # свой handler уже после его вызова
            logging.getLogger("alembic.runtime.migration").addHandler(capture)
            return result

        with application.app_context():
            command.upgrade(cfg, "b2c4d6e8f1a3")
            user = User(fio="Дубль", token="dup-token")
            db.session.add(user)
            db.session.flush()
            db.session.add(Order(user_id=user.id, order_number="DUP-1"))
            db.session.add(Order(user_id=user.id, order_number="DUP-1"))
            db.session.commit()

            with mock.patch("logging.config.fileConfig", _file_config):
                command.upgrade(cfg, "c3d5e7f9a1b4")

            assert any("Дубликаты" in message for message in warnings), warnings

            inspector = inspect(db.engine)
            assert "ix_orders_user_id_order_number" in {
                i["name"] for i in inspector.get_indexes("orders")
            }
            # unique-индекс при дублях не создан
            assert "uq_orders_user_id_order_number" not in {
                i["name"] for i in inspector.get_indexes("orders")
            }


class TestPhotoCounts:
    def test_counts_by_order_is_batched(self, db_session, regular_user):
        orders = []
        for i in range(3):
            order = Order(user_id=regular_user.id, order_number=f"PC-{i}")
            db_session.add(order)
            orders.append(order)
        db_session.commit()

        counts = PhotoService.counts_by_order([o.id for o in orders])
        assert counts == {}
        assert PhotoService.counts_by_order([]) == {}

        # Один заказ без фото, у двух — счётчики из других заказов не подмешиваются
        assert counts.get(orders[0].id, 0) == 0


class TestUploadSizeCache:
    def test_upload_size_is_cached_within_ttl(self, tmp_path):
        previous = (_UPLOAD_SIZE_CACHE["ts"], _UPLOAD_SIZE_CACHE["value"])
        try:
            _UPLOAD_SIZE_CACHE["ts"] = time.monotonic()
            _UPLOAD_SIZE_CACHE["value"] = 4242
            # Свежий кэш: файловая система не трогается
            assert _upload_size(str(tmp_path)) == 4242

            # Просроченный кэш: значение пересчитывается
            _UPLOAD_SIZE_CACHE["ts"] = time.monotonic() - _UPLOAD_SIZE_TTL - 1
            (tmp_path / "a.bin").write_bytes(b"x" * 10)
            assert _upload_size(str(tmp_path)) == 10
        finally:
            _UPLOAD_SIZE_CACHE["ts"], _UPLOAD_SIZE_CACHE["value"] = previous
