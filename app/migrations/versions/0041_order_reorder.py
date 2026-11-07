"""Track actor cancellation quotas and link replacement orders."""

import sqlalchemy as sa
from alembic import op

revision = "0041_order_reorder"
down_revision = "0040_push_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable metadata without rewriting historic orders."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.add_column("orders", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.add_column("orders", sa.Column("cancelled_by", sa.Uuid()))
    op.add_column("orders", sa.Column("replacement_order_id", sa.Uuid()))
    op.create_index(
        "idx_orders_cancelled_by_at",
        "orders",
        ["cancelled_by", "cancelled_at"],
        postgresql_where=sa.text("cancelled_by IS NOT NULL"),
    )
    op.create_unique_constraint(
        "uq_orders_replacement_order_id", "orders", ["replacement_order_id"]
    )
    op.create_foreign_key(
        "fk_orders_replacement_order_id_orders",
        "orders",
        "orders",
        ["replacement_order_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove quota and replacement metadata while retaining order records."""
    op.drop_constraint("fk_orders_replacement_order_id_orders", "orders", type_="foreignkey")
    op.drop_constraint("uq_orders_replacement_order_id", "orders", type_="unique")
    op.drop_index("idx_orders_cancelled_by_at", table_name="orders")
    op.drop_column("orders", "replacement_order_id")
    op.drop_column("orders", "cancelled_by")
    op.drop_column("orders", "cancelled_at")
