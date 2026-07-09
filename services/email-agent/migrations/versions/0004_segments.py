from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_segments"
down_revision = "0003_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_agent_segments",
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("segment_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("match", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("members", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("agent_instance_id", "segment_id", name="email_agent_segments_pkey"),
    )
    op.create_index(
        "email_agent_segments_instance_idx",
        "email_agent_segments",
        ["agent_instance_id", "segment_id"],
    )


def downgrade() -> None:
    op.drop_index("email_agent_segments_instance_idx", table_name="email_agent_segments")
    op.drop_table("email_agent_segments")
