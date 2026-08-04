from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_instance_setup"
down_revision = "0010_poll_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_agent_instance_setup",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'created'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "email_agent_instance_setup_user_instance_idx",
        "email_agent_instance_setup",
        ["user_id", "agent_instance_id"],
        unique=True,
    )

    op.create_table(
        "email_agent_instance_setup_step",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "setup_id",
            sa.BigInteger(),
            sa.ForeignKey("email_agent_instance_setup.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Denormalized from the parent so the claim query can filter/scope by
        # instance without a join, and so this table stays independently queryable.
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("step_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "email_agent_instance_setup_step_setup_key_idx",
        "email_agent_instance_setup_step",
        ["setup_id", "step_key"],
        unique=True,
    )
    op.create_index(
        "email_agent_instance_setup_step_claim_idx",
        "email_agent_instance_setup_step",
        ["status", "position", "created_at"],
    )

    # Operational setup-progress metadata only (step keys, counts, short reasons) —
    # deliberately NOT row-level-secured, same rationale as agent_poll_jobs
    # (migration 0010): claim_next_step must scan pending steps across every
    # tenant in one atomic query, and FORCE ROW LEVEL SECURITY would block that
    # even for the owning role. Per-instance reads/writes filter explicitly by
    # (user_id, agent_instance_id) in application SQL instead.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    email_agent_instance_setup, email_agent_instance_setup_step
                TO agora_email_agent;
                GRANT USAGE, SELECT ON
                    email_agent_instance_setup_id_seq, email_agent_instance_setup_step_id_seq
                TO agora_email_agent;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_index("email_agent_instance_setup_step_claim_idx", table_name="email_agent_instance_setup_step")
    op.drop_index("email_agent_instance_setup_step_setup_key_idx", table_name="email_agent_instance_setup_step")
    op.drop_table("email_agent_instance_setup_step")
    op.drop_index("email_agent_instance_setup_user_instance_idx", table_name="email_agent_instance_setup")
    op.drop_table("email_agent_instance_setup")
