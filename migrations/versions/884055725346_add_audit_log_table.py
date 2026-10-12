"""Add audit_log table

Revision ID: 884055725346
Revises: c3d5e7f9a1b4
Create Date: 2026-10-11 10:40:01.606580

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '884055725346'
down_revision = 'c3d5e7f9a1b4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'audit_log' in inspector.get_table_names():
        return
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('user_fio', sa.String(255), nullable=True),
        sa.Column('action_type', sa.String(30), nullable=False),
        sa.Column('object_type', sa.String(30), nullable=False),
        sa.Column('object_id', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('diff_json', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('request_id', sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'])
    op.create_index('ix_audit_log_user_id', 'audit_log', ['user_id'])
    op.create_index('ix_audit_log_action_type', 'audit_log', ['action_type'])
    op.create_index('ix_audit_log_object_type', 'audit_log', ['object_type'])
    op.create_index('ix_audit_log_object_id', 'audit_log', ['object_id'])
    op.create_index('ix_audit_log_request_id', 'audit_log', ['request_id'])


def downgrade():
    op.drop_index('ix_audit_log_request_id', table_name='audit_log')
    op.drop_index('ix_audit_log_object_id', table_name='audit_log')
    op.drop_index('ix_audit_log_object_type', table_name='audit_log')
    op.drop_index('ix_audit_log_action_type', table_name='audit_log')
    op.drop_index('ix_audit_log_user_id', table_name='audit_log')
    op.drop_index('ix_audit_log_created_at', table_name='audit_log')
    op.drop_table('audit_log')
