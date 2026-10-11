"""Widen users.token from 10 to 128 chars (P1-8)

Revision ID: b2c4d6e8f1a3
Revises: e7a1c3b5d9f2
Create Date: 2026-10-08 00:10:00.000000

Токен теперь `secrets.token_urlsafe(24)` = 32 символа. На PostgreSQL
`VARCHAR(10)` отклонит такую длину, поэтому колонку нужно расширить.
SQLite не ограничивает длину VARCHAR, поэтому там ничего не меняем —
изменение колонки потребовало бы batch-recreate таблицы users, на которую
ссылаются orders (риск разрыва FK).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c4d6e8f1a3"
down_revision = "e7a1c3b5d9f2"
branch_labels = None
depends_on = None

TARGET_LENGTH = 128


def _token_length(bind) -> int | None:
    inspector = sa.inspect(bind)
    if "users" not in inspector.get_table_names():
        return None
    for column in inspector.get_columns("users"):
        if column["name"] == "token":
            return getattr(column["type"], "length", None)
    return None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    length = _token_length(bind)
    if not length or length >= TARGET_LENGTH:
        return

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "token",
            type_=sa.String(length=TARGET_LENGTH),
            existing_type=sa.String(length=length),
            existing_nullable=False,
            existing_unique=True,
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    length = _token_length(bind)
    if not length or length <= 10:
        return

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "token",
            type_=sa.String(length=10),
            existing_type=sa.String(length=length),
            existing_nullable=False,
            existing_unique=True,
        )
