from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_analytics_run_fields"
down_revision = "0005_approval_assignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("decision", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE agent_runs SET created_at = updated_at WHERE created_at IS NULL")
    op.alter_column("agent_runs", "created_at", nullable=False)


def downgrade() -> None:
    op.drop_column("agent_runs", "decision_at")
    op.drop_column("agent_runs", "decision")
    op.drop_column("agent_runs", "created_at")
