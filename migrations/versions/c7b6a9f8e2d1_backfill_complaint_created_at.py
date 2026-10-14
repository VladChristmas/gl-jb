"""Backfill complaints.created_at из зеркала/даты заказа

Все жалобы, перенесённые из зеркала `orders.complaint_*`, получили
created_at = orders.complaint_date, а в исходном Excel дата жалобы не
заполнялась — в проде 35/35 жалоб с NULL: «Дата: —» в списке и полная
пустота в аналитике при любом периоде (фильтры честно отсекают NULL).

Заполняем NULL только для строк, у которых есть хотя бы одна дата заказа:
1) `orders.complaint_date` — реальная дата обращения, если появилась позже;
2) `orders.created_at` — дата импорта заказа (лучшее доступное приближение).

Существующие created_at не трогаем. Downgrade не восстанавливает NULL —
исходных NULL-дат не существовало, восстанавливать нечего.

Revision ID: c7b6a9f8e2d1
Revises: add_operational_analysis
Create Date: 2026-10-14 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7b6a9f8e2d1"
down_revision = "add_operational_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "complaints" not in tables or "orders" not in tables:
        return

    order_cols = {col["name"] for col in inspector.get_columns("orders")}
    if "created_at" not in order_cols:
        return
    has_mirror_date = "complaint_date" in order_cols

    if has_mirror_date:
        set_expr = (
            "COALESCE("
            "(SELECT o.complaint_date FROM orders o WHERE o.id = complaints.order_id), "
            "(SELECT o.created_at FROM orders o WHERE o.id = complaints.order_id)"
            ")"
        )
        date_filter = (
            "(o.complaint_date IS NOT NULL OR o.created_at IS NOT NULL)"
        )
    else:
        set_expr = "(SELECT o.created_at FROM orders o WHERE o.id = complaints.order_id)"
        date_filter = "o.created_at IS NOT NULL"

    bind.execute(
        sa.text(
            f"UPDATE complaints SET created_at = {set_expr} "
            "WHERE complaints.created_at IS NULL "
            f"AND EXISTS (SELECT 1 FROM orders o WHERE o.id = complaints.order_id "
            f"AND {date_filter})"
        )
    )


def downgrade() -> None:
    # Данные не откатываются: NULL-даты жалоб не существовали до бэкоффила.
    pass
