"""Add FK indexes and unique constraints (P1-12, P2-3, P2-4)

Revision ID: c3d5e7f9a1b4
Revises: b2c4d6e8f1a3
Create Date: 2026-10-08 00:20:00.000000

- `/uploads/<filename>` проверяет владельца (запрос по photos.filename и
  джойн orders через photos.order_id) — нужны индексы;
- гонка импорта заказов закрывается unique (user_id, order_number);
- users.token должен быть уникален на уровне БД.

Всё идемпотентно: индексы могли быть созданы заранее, а на живой базе
могут быть дубли — тогда вместо unique создаётся обычный индекс и пишется
предупреждение (индекс не должен ронять прод-старт).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d5e7f9a1b4"
down_revision = "b2c4d6e8f1a3"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PLAIN_INDEXES = (
    ("ix_photos_order_id", "photos", ["order_id"]),
    ("ix_photos_filename", "photos", ["filename"]),
    ("ix_orders_user_id", "orders", ["user_id"]),
)

UNIQUE_INDEXES = (
    ("uq_orders_user_id_order_number", "orders", ["user_id", "order_number"]),
    ("uq_users_token", "users", ["token"]),
)


def _has_unique_on(bind, table: str, columns: tuple) -> bool:
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints(table):
        if tuple(constraint.get("column_names") or ()) == columns:
            return True
    for index in inspector.get_indexes(table):
        if index.get("unique") and tuple(index.get("column_names") or ()) == columns:
            return True
    return False


def _has_duplicates(bind, table: str, columns: tuple) -> bool:
    cols = ", ".join(columns)
    where = " AND ".join(f"{col} IS NOT NULL" for col in columns)
    row = bind.execute(
        sa.text(
            f"SELECT {cols} FROM {table} WHERE {where} "
            f"GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    return row is not None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for name, table, columns in PLAIN_INDEXES:
        if table not in tables:
            continue
        existing = {index["name"] for index in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns, unique=False)

    for name, table, columns in UNIQUE_INDEXES:
        if table not in tables:
            continue
        if _has_unique_on(bind, table, tuple(columns)):
            continue
        if _has_duplicates(bind, table, tuple(columns)):
            logger.warning(
                "Дубликаты в %s(%s) — уникальный индекс %s не создан, "
                "создан обычный. Удалите дубли и перезапустите миграцию.",
                table,
                ", ".join(columns),
                name,
            )
            fallback = f"ix_{table}_{'_'.join(columns)}"
            existing = {index["name"] for index in inspector.get_indexes(table)}
            if fallback not in existing:
                op.create_index(fallback, table, columns, unique=False)
            continue
        op.create_index(name, table, columns, unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for name, table, _columns in UNIQUE_INDEXES + PLAIN_INDEXES:
        if table not in tables:
            continue
        existing = {index["name"] for index in inspector.get_indexes(table)}
        if name in existing:
            op.drop_index(name, table_name=table)
