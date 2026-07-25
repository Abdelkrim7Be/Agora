from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_contact_categories"
down_revision = "0012_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_agent_contacts", sa.Column("category", sa.Text(), nullable=True))
    op.add_column("email_agent_contacts", sa.Column("domain", sa.Text(), nullable=True))
    op.add_column("email_agent_contacts", sa.Column("priority", sa.Text(), nullable=True))
    op.add_column(
        "email_agent_contacts",
        sa.Column("category_source", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
    )
    op.add_column("email_agent_contacts", sa.Column("category_confidence", sa.Float(), nullable=True))
    op.create_index(
        "email_agent_contacts_instance_category_idx",
        "email_agent_contacts",
        ["agent_instance_id", "category"],
    )


def downgrade() -> None:
    op.drop_index("email_agent_contacts_instance_category_idx", table_name="email_agent_contacts")
    op.drop_column("email_agent_contacts", "category_confidence")
    op.drop_column("email_agent_contacts", "category_source")
    op.drop_column("email_agent_contacts", "priority")
    op.drop_column("email_agent_contacts", "domain")
    op.drop_column("email_agent_contacts", "category")
