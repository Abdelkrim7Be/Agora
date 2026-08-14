"""Scope agent_runs visibility to instance access, not literal run authorship.

The 0009 policy required BOTH agent_instance_id and user_id to match the
session before a row was visible at all -- including for plain SELECTs, since
the policy is FOR ALL. That means an instance's owner (or a platform admin
with a real gateway grant on the instance) could not see a run triggered
under a different person's name on the same instance: two people validating
the same shared mailbox is the normal case for this product, not an edge
case, and the gateway's own instance-role grants are what is supposed to
govern who can reach an instance at all. Re-scoping agent_runs to instance
access only; the other five tables from 0009 keep the stricter per-user
predicate (they hold per-user sync cursors, cost ledgers and traces, not
shared approval-queue items, so narrowing them is a separate decision).
"""

from __future__ import annotations

from alembic import op

revision = "0016_run_visibility_by_instance"
down_revision = "0015_run_sensitive_reason"
branch_labels = None
depends_on = None

INSTANCE_PREDICATE = (
    "agent_instance_id = "
    "NULLIF(current_setting('agora.agent_instance_id', true), '')"
)
USER_AND_INSTANCE_PREDICATE = (
    "(user_id = NULLIF(current_setting('agora.user_id', true), '')) "
    f"AND ({INSTANCE_PREDICATE})"
)


def upgrade() -> None:
    op.execute('DROP POLICY IF EXISTS agora_tenant_isolation ON "agent_runs"')
    op.execute(
        'CREATE POLICY agora_tenant_isolation ON "agent_runs" '
        f"FOR ALL USING ({INSTANCE_PREDICATE}) WITH CHECK ({INSTANCE_PREDICATE})"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS agora_tenant_isolation ON "agent_runs"')
    op.execute(
        'CREATE POLICY agora_tenant_isolation ON "agent_runs" '
        f"FOR ALL USING ({USER_AND_INSTANCE_PREDICATE}) WITH CHECK ({USER_AND_INSTANCE_PREDICATE})"
    )
