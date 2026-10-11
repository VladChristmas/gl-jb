"""Add operational analysis tables

Revision ID: add_operational_analysis
Revises: add_perf_indexes
Create Date: 2026-10-11

Tables added:
- operational_analysis_sessions: сессии загрузки и анализа Excel-файлов
- picker_metrics: агрегированные метрики сборщиков
- assembly_orders: детализация собранных заказов
- data_quality_issues: проблемы качества данных
"""
import sqlalchemy as sa
from alembic import op

revision = "add_operational_analysis"
down_revision = "add_perf_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_analysis_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("status", sa.String(50), default="processing"),
        sa.Column("total_complaints", sa.Integer(), default=0),
        sa.Column("total_orders", sa.Integer(), default=0),
        sa.Column("total_assembly_orders", sa.Integer(), default=0),
        sa.Column("matched_complaints", sa.Integer(), default=0),
        sa.Column("matched_orders", sa.Integer(), default=0),
        sa.Column("matched_assembly", sa.Integer(), default=0),
        sa.Column("error_count", sa.Integer(), default=0),
        sa.Column("warning_count", sa.Integer(), default=0),
        sa.Column("wait_threshold_minutes", sa.Integer(), default=30),
    )

    op.create_table(
        "picker_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("operational_analysis_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("picker_fio", sa.String(255), nullable=False),
        sa.Column("unique_orders", sa.Integer(), default=0),
        sa.Column("total_assembly_time_minutes", sa.Float(), default=0.0),
        sa.Column("avg_assembly_time_minutes", sa.Float(), default=0.0),
        sa.Column("median_assembly_time_minutes", sa.Float(), default=0.0),
        sa.Column("speed_orders_per_minute", sa.Float(), default=0.0),
        sa.Column("speed_items_per_minute", sa.Float(), default=0.0),
        sa.Column("total_items", sa.Integer(), default=0),
        sa.Column("avg_wait_time_minutes", sa.Float(), default=0.0),
        sa.Column("median_wait_time_minutes", sa.Float(), default=0.0),
        sa.Column("min_wait_time_minutes", sa.Float(), default=0.0),
        sa.Column("max_wait_time_minutes", sa.Float(), default=0.0),
        sa.Column("orders_above_wait_threshold", sa.Integer(), default=0),
        sa.Column("orders_with_complaints", sa.Integer(), default=0),
        sa.Column("total_complaints", sa.Integer(), default=0),
        sa.Column("complaint_rate", sa.Float(), default=0.0),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["operational_analysis_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_picker_metrics_session_id", "picker_metrics", ["session_id"])
    op.create_index("ix_picker_metrics_picker_fio", "picker_metrics", ["picker_fio"])

    op.create_table(
        "assembly_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("operational_analysis_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.String(100), nullable=False),
        sa.Column("picker_fio", sa.String(255), nullable=False),
        sa.Column("assembly_start", sa.DateTime()),
        sa.Column("assembly_end", sa.DateTime()),
        sa.Column("assembly_duration_minutes", sa.Float()),
        sa.Column("order_created_at", sa.DateTime()),
        sa.Column("wait_time_minutes", sa.Float()),
        sa.Column("items_count", sa.Integer()),
        sa.Column("has_complaint", sa.Boolean(), default=False),
        sa.Column("complaint_count", sa.Integer(), default=0),
        sa.Column("match_status", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["operational_analysis_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assembly_orders_session_id", "assembly_orders", ["session_id"])
    op.create_index("ix_assembly_orders_order_id", "assembly_orders", ["order_id"])
    op.create_index("ix_assembly_orders_picker_fio", "assembly_orders", ["picker_fio"])

    op.create_table(
        "data_quality_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("operational_analysis_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("issue_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source_sheet", sa.String(50)),
        sa.Column("row_number", sa.Integer()),
        sa.Column("order_id", sa.String(100)),
        sa.Column("picker_fio", sa.String(255)),
        sa.Column("severity", sa.String(20), default="warning"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["operational_analysis_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_data_quality_issues_session_id", "data_quality_issues", ["session_id"])


def downgrade():
    op.drop_table("data_quality_issues")
    op.drop_table("assembly_orders")
    op.drop_table("picker_metrics")
    op.drop_table("operational_analysis_sessions")
