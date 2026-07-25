from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_notifications"
down_revision = "0011_instance_setup"
branch_labels = None
depends_on = None

USER_PREDICATE = "user_id = NULLIF(current_setting('agora.user_id', true), '')"
INSTANCE_PREDICATE = (
    "agent_instance_id = NULLIF(current_setting('agora.agent_instance_id', true), '')"
)


def upgrade() -> None:
    op.create_table(
        "email_agent_notification",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("notification_type", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'info'")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("action_url", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "email_agent_notification_list_idx",
        "email_agent_notification",
        ["user_id", "agent_instance_id", sa.text("created_at DESC")],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX email_agent_notification_dedupe_idx
        ON email_agent_notification (user_id, agent_instance_id, dedupe_key)
        WHERE read_at IS NULL AND dedupe_key IS NOT NULL
        """
    )

    # Holds user_id + free-text bodies (PII) -> RLS, same pattern as 0009_tenant_rls.
    op.execute('ALTER TABLE "email_agent_notification" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "email_agent_notification" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY agora_tenant_isolation ON "email_agent_notification" '
        f'FOR ALL USING (({USER_PREDICATE}) AND ({INSTANCE_PREDICATE})) '
        f'WITH CHECK (({USER_PREDICATE}) AND ({INSTANCE_PREDICATE}))'
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON email_agent_notification TO agora_email_agent;
                GRANT USAGE, SELECT ON email_agent_notification_id_seq TO agora_email_agent;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS agora_tenant_isolation ON "email_agent_notification"')
    op.execute('ALTER TABLE "email_agent_notification" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "email_agent_notification" DISABLE ROW LEVEL SECURITY')
    op.execute("DROP INDEX IF EXISTS email_agent_notification_dedupe_idx")
    op.drop_index("email_agent_notification_list_idx", table_name="email_agent_notification")
    op.drop_table("email_agent_notification")
