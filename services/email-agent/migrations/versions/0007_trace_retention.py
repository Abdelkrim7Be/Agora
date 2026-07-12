from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_trace_retention"
down_revision = "0006_analytics_run_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_traces",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("node", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_eur", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.UniqueConstraint("event_id", name="llm_traces_event_id_key"),
    )
    op.create_index(
        "llm_traces_run_timestamp_idx",
        "llm_traces",
        ["agent_instance_id", "run_id", sa.text("timestamp ASC")],
    )


def downgrade() -> None:
    op.drop_index("llm_traces_run_timestamp_idx", table_name="llm_traces")
    op.drop_table("llm_traces")
