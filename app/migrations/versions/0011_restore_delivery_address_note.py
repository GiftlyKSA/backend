"""Restore address notes while leaving the removed delivery URL absent."""

import sqlalchemy as sa
from alembic import op

revision = "0011_restore_address_note"
down_revision = "0010_remove_delivery_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivery_address_note", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "delivery_address_note")
