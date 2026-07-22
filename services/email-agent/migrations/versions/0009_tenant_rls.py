from __future__ import annotations

from alembic import op

revision = "0009_tenant_rls"
down_revision = "0008_dlq"
branch_labels = None
depends_on = None

USER_AND_INSTANCE_TABLES = (
    "agent_runs",
    "gmail_sync_state",
    "email_agent_sync",
    "llm_costs",
    "llm_traces",
    "email_agent_dlq",
)

INSTANCE_TABLES = (
    "email_agent_instance_config",
    "email_agent_roles",
    "email_agent_contacts",
    "email_agent_segments",
)

USER_PREDICATE = "user_id = NULLIF(current_setting('agora.user_id', true), '')"
INSTANCE_PREDICATE = (
    "agent_instance_id = "
    "NULLIF(current_setting('agora.agent_instance_id', true), '')"
)


def _enable_rls(table: str, predicate: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY agora_tenant_isolation ON "{table}" '
        f'FOR ALL USING ({predicate}) WITH CHECK ({predicate})'
    )


def upgrade() -> None:
    for table in USER_AND_INSTANCE_TABLES:
        _enable_rls(table, f"({USER_PREDICATE}) AND ({INSTANCE_PREDICATE})")
    for table in INSTANCE_TABLES:
        _enable_rls(table, INSTANCE_PREDICATE)

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    agent_runs, gmail_sync_state, email_agent_sync, llm_costs,
                    llm_traces, email_agent_dlq, email_agent_instance_config,
                    email_agent_roles, email_agent_contacts, email_agent_segments
                TO agora_email_agent;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
                TO agora_email_agent;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in (*USER_AND_INSTANCE_TABLES, *INSTANCE_TABLES):
        op.execute(f'DROP POLICY IF EXISTS agora_tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
