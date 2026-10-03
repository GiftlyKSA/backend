"""Keep delivery-date validity stable throughout an order's lifetime."""

from alembic import op

revision = "0013_fixed_delivery_window"
down_revision = "0012_invoice_promo_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Anchor the window to the stored UTC creation date, preserving order data."""
    op.drop_constraint("chk_delivery_date_window", "orders", type_="check")
    op.create_check_constraint(
        "chk_delivery_date_window",
        "orders",
        "delivery_date >= (created_at AT TIME ZONE 'UTC')::date "
        "AND delivery_date <= (created_at AT TIME ZONE 'UTC')::date + 180",
    )


def downgrade() -> None:
    """Restore the old check without rejecting historical rows during rollback."""
    op.drop_constraint("chk_delivery_date_window", "orders", type_="check")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT chk_delivery_date_window "
        "CHECK (delivery_date >= CURRENT_DATE "
        "AND delivery_date <= CURRENT_DATE + INTERVAL '180 days') NOT VALID"
    )
