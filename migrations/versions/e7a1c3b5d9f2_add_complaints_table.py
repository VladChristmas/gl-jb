"""Add complaints table (один заказ — много жалоб)

Revision ID: e7a1c3b5d9f2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-08 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e7a1c3b5d9f2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _backfill(bind):
    """Переносит жалобы из зеркала `orders.complaint_*` в строки `complaints`.

    Старый код склеивал тексты через "; ", а статус и дата были общими
    на всю склейку — переносим как есть. Идемпотентно: заказ, у которого
    жалобы уже есть, повторно не трогаем.
    """
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "orders" not in tables or "complaints" not in tables:
        return

    already_migrated = {
        row[0] for row in bind.execute(sa.text("SELECT DISTINCT order_id FROM complaints"))
    }

    rows = bind.execute(
        sa.text(
            "SELECT id, complaint_text, complaint_status, complaint_date FROM orders "
            "WHERE complaint_text IS NOT NULL AND complaint_text <> ''"
        )
    ).fetchall()

    for order_id, text, status, complaint_date in rows:
        if order_id in already_migrated:
            continue
        for chunk in text.split("; "):
            part = chunk.strip()
            if not part:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO complaints (order_id, text, status, created_at) "
                    "VALUES (:order_id, :text, :status, :created_at)"
                ),
                {
                    "order_id": order_id,
                    "text": part,
                    "status": status,
                    "created_at": complaint_date,
                },
            )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "complaints" not in inspector.get_table_names():
        op.create_table(
            "complaints",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_complaints_order_id", "complaints", ["order_id"], unique=False)

    _backfill(bind)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "complaints" in inspector.get_table_names():
        op.drop_index("ix_complaints_order_id", table_name="complaints")
        op.drop_table("complaints")
