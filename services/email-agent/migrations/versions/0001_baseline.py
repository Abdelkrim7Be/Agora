from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False, server_default=sa.text("'default-email-agent'")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("pending_action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("email_id", sa.Text(), nullable=True),
        sa.Column("gmail_thread_id", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("category_display_name", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("template", sa.Text(), nullable=True),
        sa.Column("workflow_owner", sa.Text(), nullable=True),
        sa.Column("workflow_approver", sa.Text(), nullable=True),
        sa.Column("workflow_route_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        "agent_runs_user_instance_status_updated_idx",
        "agent_runs",
        ["user_id", "agent_instance_id", "status", sa.text("updated_at DESC")],
    )
    op.create_index(
        "agent_runs_user_instance_updated_idx",
        "agent_runs",
        ["user_id", "agent_instance_id", sa.text("updated_at DESC")],
    )
    op.create_index(
        "agent_runs_instance_status_updated_idx",
        "agent_runs",
        ["agent_instance_id", "status", sa.text("updated_at DESC")],
    )

    op.create_table(
        "gmail_sync_state",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False, server_default=sa.text("'default-email-agent'")),
        sa.Column("history_id", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("user_id", "agent_instance_id", name="gmail_sync_state_pkey"),
    )

    op.create_table(
        "email_agent_sync",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'gmail'")),
        sa.Column("connection_status", sa.Text(), nullable=False, server_default=sa.text("'disconnected'")),
        sa.Column("sync_mode", sa.Text(), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("watch_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("user_id", "agent_instance_id", name="email_agent_sync_pkey"),
    )

    op.create_table(
        "llm_costs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("node", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_eur", sa.Float(), nullable=False),
        sa.UniqueConstraint("event_id", name="llm_costs_event_id_key"),
    )
    op.create_index(
        "llm_costs_user_instance_timestamp_idx",
        "llm_costs",
        ["user_id", "agent_instance_id", sa.text("timestamp DESC")],
    )

    op.create_table(
        "email_agent_instance_config",
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("config_kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("agent_instance_id", "config_kind", name="email_agent_instance_config_pkey"),
    )


def downgrade() -> None:
    op.drop_table("email_agent_instance_config")
    op.drop_index("llm_costs_user_instance_timestamp_idx", table_name="llm_costs")
    op.drop_table("llm_costs")
    op.drop_table("email_agent_sync")
    op.drop_table("gmail_sync_state")
    op.drop_index("agent_runs_instance_status_updated_idx", table_name="agent_runs")
    op.drop_index("agent_runs_user_instance_updated_idx", table_name="agent_runs")
    op.drop_index("agent_runs_user_instance_status_updated_idx", table_name="agent_runs")
    op.drop_table("agent_runs")
