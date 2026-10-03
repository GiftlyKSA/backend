"""Remove precise delivery-location data from orders."""

import sqlalchemy as sa
from alembic import op

revision = "0010_remove_delivery_location"
down_revision = "0009_action_only_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("orders", "delivery_map_url")
    op.drop_column("orders", "delivery_address_note")


def downgrade() -> None:
    op.add_column("orders", sa.Column("delivery_address_note", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("delivery_map_url", sa.String(2048), nullable=True))
