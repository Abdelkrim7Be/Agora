from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_run_sensitive_reason"
down_revision = "0014_run_junk_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Why a message was kept out of the agent pipeline before its body was fetched.
    op.add_column("agent_runs", sa.Column("sensitive_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "sensitive_reason")
