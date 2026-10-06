"""Add courier and picker fields to Order

Revision ID: b7e2f9a41c05
Revises: 1e17f3befee1
Create Date: 2026-10-06 18:45:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7e2f9a41c05'
down_revision = '1e17f3befee1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('courier_fio', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('address', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('delivered_at', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('picker_fio', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('pick_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('wait_time', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('pick_speed', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('pick_speed')
        batch_op.drop_column('wait_time')
        batch_op.drop_column('pick_count')
        batch_op.drop_column('picker_fio')
        batch_op.drop_column('delivered_at')
        batch_op.drop_column('address')
        batch_op.drop_column('courier_fio')
