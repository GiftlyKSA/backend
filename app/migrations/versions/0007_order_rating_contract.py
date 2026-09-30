"""Allow orders without a delivery map link."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_order_rating_contract"
down_revision = "0006_user_city_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Permit customers to provide an address without a map link."""
    op.alter_column("orders", "delivery_map_url", existing_type=sa.String(2048), nullable=True)


def downgrade() -> None:
    """Restore the prior contract once missing links have been populated."""
    op.alter_column("orders", "delivery_map_url", existing_type=sa.String(2048), nullable=False)
