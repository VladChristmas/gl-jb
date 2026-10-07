"""add role column to users

Revision ID: a1b2c3d4e5f6
Revises: c9f4e2a7d1b3
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "c9f4e2a7d1b3"
branch_labels = None
depends_on = None


def upgrade():
    # Идемпотентно: колонка могла быть добавлена ensure-блоком при старте
    inspector = sa.inspect(op.get_bind())
    if "users" not in inspector.get_table_names():
        return
    if "role" in {col["name"] for col in inspector.get_columns("users")}:
        return
    op.add_column(
        "users",
        sa.Column("role", sa.String(20), nullable=False, server_default="courier"),
    )


def downgrade():
    op.drop_column("users", "role")
