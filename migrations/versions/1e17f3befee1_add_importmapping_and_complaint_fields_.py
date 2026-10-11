"""Add ImportMapping and complaint fields to Order

Revision ID: 1e17f3befee1
Revises: af9df96d08da
Create Date: 2026-10-04 15:58:21.359345

Идемпотентно: на живой базе эти колонки могли быть созданы заранее.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '1e17f3befee1'
down_revision = 'af9df96d08da'
branch_labels = None
depends_on = None


def _columns(bind, table):
    if table not in sa.inspect(bind).get_table_names():
        return set()
    return {col['name'] for col in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'import_mappings' not in inspector.get_table_names():
        op.create_table(
            'import_mappings',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('type', sa.String(length=20), nullable=False),
            sa.Column('id_column', sa.String(length=100), nullable=False),
            sa.Column('fio_column', sa.String(length=100), nullable=True),
            sa.Column('order_number_column', sa.String(length=100), nullable=True),
            sa.Column('field_mapping', sa.JSON(), nullable=True),
            sa.Column('skip_first_row', sa.Boolean(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                      nullable=True),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'),
                      nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    existing = _columns(bind, 'orders')
    if not existing:
        return

    wanted = (
        ('complaint_text', sa.Text(), None),
        ('complaint_status', sa.String(length=50), None),
        ('complaint_date', sa.DateTime(), None),
        ('external_id', sa.String(length=100), None),
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        for name, type_, _default in wanted:
            if name not in existing:
                batch_op.add_column(sa.Column(name, type_, nullable=True))

    if 'external_id' in _columns(bind, 'orders'):
        index_names = {ix['name'] for ix in inspector.get_indexes('orders')}
        if 'ix_orders_external_id' not in index_names:
            op.create_index(op.f('ix_orders_external_id'), 'orders', ['external_id'], unique=False)


def downgrade():
    bind = op.get_bind()
    existing = _columns(bind, 'orders')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        for name in ('external_id', 'complaint_date', 'complaint_status', 'complaint_text'):
            if name in existing:
                batch_op.drop_column(name)
