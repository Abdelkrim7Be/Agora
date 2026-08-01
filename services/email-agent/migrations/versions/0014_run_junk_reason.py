from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_run_junk_reason"
down_revision = "0013_contact_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Why the junk gate discarded a message. The poller already decided it — the
    # bulk header, the Gmail category, the configured block list — but the
    # verdict had nowhere to live, so a gated run showed up as "ignored" with no
    # way to tell whether that was right. Auditing the gate needs the reason.
    op.add_column("agent_runs", sa.Column("junk_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "junk_reason")
