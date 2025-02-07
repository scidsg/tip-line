"""add user email settings

Revision ID: 6071f1eea074
Revises: 4a53667aff6e
Create Date: 2025-02-06 10:23:10.939648

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "6071f1eea074"
down_revision = "4a53667aff6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "enable_email_notifications",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "email_include_message_content",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("email_include_message_content")
        batch_op.drop_column("enable_email_notifications")
