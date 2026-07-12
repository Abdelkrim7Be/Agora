from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_approval_assignment"
down_revision = "0004_segments"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("assignee", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_dept", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("agent_runs", "workflow_dept")
    op.drop_column("agent_runs", "assignee")
