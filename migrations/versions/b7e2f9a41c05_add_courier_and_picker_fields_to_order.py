"""Add courier and picker fields to Order

Revision ID: b7e2f9a41c05
Revises: 1e17f3befee1
Create Date: 2026-10-06 18:45:00.000000

Идемпотентно: колонки могли быть добавлены ensure-блоком при старте приложения.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7e2f9a41c05'
down_revision = '1e17f3befee1'
branch_labels = None
depends_on = None

_COLUMNS = (
    ('courier_fio', sa.String(length=255)),
    ('address', sa.String(length=500)),
    ('delivered_at', sa.String(length=100)),
    ('picker_fio', sa.String(length=255)),
    ('pick_count', sa.Integer()),
    ('wait_time', sa.String(length=100)),
    ('pick_speed', sa.String(length=100)),
)


def _columns(table):
    bind = op.get_bind()
    if table not in sa.inspect(bind).get_table_names():
        return set()
    return {col['name'] for col in sa.inspect(bind).get_columns(table)}


def upgrade():
    existing = _columns('orders')
    if not existing:
        return
    with op.batch_alter_table('orders', schema=None) as batch_op:
        for name, type_ in _COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade():
    existing = _columns('orders')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        for name, _type in reversed(_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
