"""Index admin audit browsing and the recent-orders chart."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_audit_trail_indexes"
down_revision = "0007_order_rating_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index audit browsing and the bounded recent-orders chart."""
    op.create_index("idx_orders_created_at", "orders", [sa.text("created_at DESC")])
    op.create_index(
        "idx_audit_logs_created_id",
        "audit_logs",
        [sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "idx_audit_logs_category_created",
        "audit_logs",
        [
            sa.text("(metadata ->> 'actor_category')"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
    )


def downgrade() -> None:
    """Remove the additional browse indexes."""
    op.drop_index("idx_audit_logs_category_created", table_name="audit_logs")
    op.drop_index("idx_audit_logs_created_id", table_name="audit_logs")
    op.drop_index("idx_orders_created_at", table_name="orders")
