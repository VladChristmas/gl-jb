"""Initial migration — базовая схема (users / orders / photos)

Revision ID: af9df96d08da
Revises:
Create Date: 2026-10-04 12:35:19.021255

До исправления ревизия содержала только batch_alter_table() и на чистой базе
падала с NoSuchTableError: orders — приложение выживало исключительно за счёт
запрещённого db.create_all() fallback, а на PostgreSQL create_all не меняет
существующие таблицы (схема разъезжалась).

Теперь: create-if-missing. Существующая база (таблицы уже есть) не трогается,
последующие ревизии идемпотентны.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'af9df96d08da'
down_revision = None
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade():
    inspector = _inspector()
    existing = set(inspector.get_table_names())

    if 'users' not in existing:
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('fio', sa.String(length=255), nullable=False),
            sa.Column('token', sa.String(length=10), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                      nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('fio'),
            sa.UniqueConstraint('token'),
        )

    if 'orders' not in existing:
        op.create_table(
            'orders',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('order_number', sa.String(length=100), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                      nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        )

    if 'photos' not in existing:
        op.create_table(
            'photos',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('order_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(length=255), nullable=False),
            sa.Column('original_filename', sa.String(length=255), nullable=False),
            sa.Column('uploaded_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                      nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        )


def downgrade():
    inspector = _inspector()
    existing = set(inspector.get_table_names())
    for table in ('photos', 'orders', 'users'):
        if table in existing:
            op.drop_table(table)
