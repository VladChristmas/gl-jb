"""Add messages table

Revision ID: c9f4e2a7d1b3
Revises: b7e2f9a41c05
Create Date: 2026-10-07 00:30:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c9f4e2a7d1b3'
down_revision = 'b7e2f9a41c05'
branch_labels = None
depends_on = None


def upgrade():
    # Идемпотентно: таблица могла быть создана db.create_all() при старте
    inspector = sa.inspect(op.get_bind())
    if 'messages' in inspector.get_table_names():
        return
    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('messages')
