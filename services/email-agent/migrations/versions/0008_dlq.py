from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_dlq"
down_revision = "0007_trace_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_agent_dlq",
        sa.Column("entry_id", sa.Text(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("message_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("gmail_thread_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'dead_letter'")),
        sa.Column("requeue_token", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("requeued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "email_agent_dlq_instance_status_timestamp_idx",
        "email_agent_dlq",
        ["agent_instance_id", "status", sa.text("timestamp DESC")],
    )


def downgrade() -> None:
    op.drop_index("email_agent_dlq_instance_status_timestamp_idx", table_name="email_agent_dlq")
    op.drop_table("email_agent_dlq")
