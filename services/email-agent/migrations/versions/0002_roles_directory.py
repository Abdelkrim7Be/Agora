from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_roles_directory"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_agent_roles",
        sa.Column("agent_instance_id", sa.Text(), nullable=False),
        sa.Column("role_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("dept", sa.Text(), nullable=True),
        sa.Column("emails", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("agent_instance_id", "role_key", name="email_agent_roles_pkey"),
    )
    op.create_index(
        "email_agent_roles_instance_dept_idx",
        "email_agent_roles",
        ["agent_instance_id", "dept", "role_key"],
    )


def downgrade() -> None:
    op.drop_index("email_agent_roles_instance_dept_idx", table_name="email_agent_roles")
    op.drop_table("email_agent_roles")
