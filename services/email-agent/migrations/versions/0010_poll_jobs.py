from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_poll_jobs"
down_revision = "0009_tenant_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_poll_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # One active (pending/processing) job per instance+message — producer re-detection
    # across cycles is idempotent; ON CONFLICT DO NOTHING relies on this index.
    op.execute(
        """
        CREATE UNIQUE INDEX agent_poll_jobs_active_message_idx
        ON agent_poll_jobs (instance_id, message_id)
        WHERE status IN ('pending', 'processing')
        """
    )
    op.create_index(
        "agent_poll_jobs_claim_idx",
        "agent_poll_jobs",
        ["status", "created_at"],
    )

    # Operational queue metadata only (instance_id + Gmail message_id, no email
    # content/PII) — deliberately NOT row-level-secured like the tenant business
    # tables in 0009_tenant_rls. A worker must claim across every instance in one
    # atomic query; RLS would scope a connection to a single tenant and break
    # cross-tenant fairness. Downstream processing still runs inside
    # agent_instance_context(job.instance_id), so every tenant-scoped table the
    # job touches after the claim stays RLS-enforced as usual.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent') THEN
                GRANT SELECT, INSERT, UPDATE ON agent_poll_jobs TO agora_email_agent;
                GRANT USAGE, SELECT ON SEQUENCE agent_poll_jobs_id_seq TO agora_email_agent;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_index("agent_poll_jobs_claim_idx", table_name="agent_poll_jobs")
    op.execute("DROP INDEX IF EXISTS agent_poll_jobs_active_message_idx")
    op.drop_table("agent_poll_jobs")
