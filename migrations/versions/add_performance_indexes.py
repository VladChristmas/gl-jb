"""Add performance indexes for common query patterns

Revision ID: add_perf_indexes
Revises: 884055725346
Create Date: 2026-10-11

Indexes added:
- ix_orders_courier_fio: для фильтрации/группировки по курьеру
- ix_orders_picker_fio: для фильтрации/группировки по сборщику
- ix_orders_created_at: для диапазонов дат
- ix_orders_complaint_text: для ILIKE поиска (через pg_trgm если доступно)
- ix_complaints_created_at: для диапазонов дат жалоб
- ix_orders_user_created: композитный для user_id + created_at
"""
import logging
import sqlalchemy as sa
from alembic import op

revision = "add_perf_indexes"
down_revision = "884055725346"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

INDEXES = (
    # Основные фильтры и группировки
    ("ix_orders_courier_fio", "orders", ["courier_fio"], False),
    ("ix_orders_picker_fio", "orders", ["picker_fio"], False),
    # Диапазоны дат
    ("ix_orders_created_at", "orders", ["created_at"], False),
    ("ix_complaints_created_at", "complaints", ["created_at"], False),
    # Композитные для частых паттернов
    ("ix_orders_user_created", "orders", ["user_id", "created_at"], False),
    ("ix_orders_courier_created", "orders", ["courier_fio", "created_at"], False),
    ("ix_orders_picker_created", "orders", ["picker_fio", "created_at"], False),
    ("ix_complaints_order_created", "complaints", ["order_id", "created_at"], False),
    ("ix_photos_order_id", "photos", ["order_id"], False),  # уже есть, но идемпотентно
)


def _inspector():
    return sa.inspect(op.get_bind())


def _index_exists(bind, table: str, name: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return name in {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for name, table, columns, unique in INDEXES:
        if table not in tables:
            logger.info("Table %s not found, skipping index %s", table, name)
            continue
        if _index_exists(bind, table, name):
            logger.info("Index %s already exists on %s", name, table)
            continue
        try:
            op.create_index(name, table, columns, unique=unique)
            logger.info("Created index %s on %s(%s)", name, table, ", ".join(columns))
        except Exception as e:
            logger.warning("Failed to create index %s on %s: %s", name, table, e)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for name, table, _columns, _unique in reversed(INDEXES):
        if table not in tables:
            continue
        if _index_exists(bind, table, name):
            try:
                op.drop_index(name, table_name=table)
                logger.info("Dropped index %s on %s", name, table)
            except Exception as e:
                logger.warning("Failed to drop index %s on %s: %s", name, table, e)