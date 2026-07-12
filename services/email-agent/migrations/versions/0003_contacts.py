from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_contacts"
down_revision = "0002_roles_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_agent_contacts",
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("agent_instance_id", "email", name="email_agent_contacts_pkey"),
    )
    op.create_index(
        "email_agent_contacts_instance_audience_idx",
        "email_agent_contacts",
        ["agent_instance_id", "audience", "active"],
    )


def downgrade() -> None:
    op.drop_index("email_agent_contacts_instance_audience_idx", table_name="email_agent_contacts")
    op.drop_table("email_agent_contacts")
