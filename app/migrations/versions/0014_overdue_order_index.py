"""Index the bounded overdue unaccepted-order sweep."""

import sqlalchemy as sa
from alembic import op

revision = "0014_overdue_order_index"
down_revision = "0013_fixed_delivery_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index only unaccepted orders by their expiry scan order."""
    op.create_index(
        "idx_orders_overdue_unaccepted",
        "orders",
        ["delivery_date", "id"],
        postgresql_where=sa.text("status = 'NEW' AND courier_id IS NULL"),
    )


def downgrade() -> None:
    """Remove the optimization without changing order records."""
    op.drop_index("idx_orders_overdue_unaccepted", table_name="orders")
