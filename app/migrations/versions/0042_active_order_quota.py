"""Add the measured partial index for the customer active-order quota."""

import sqlalchemy as sa
from alembic import context, op

revision = "0042_active_order_quota"
down_revision = "0041_order_reorder"
branch_labels = None
depends_on = None

_INDEX = "idx_orders_customer_active_quota"
_PREDICATE = (
    "status IN ('NEW', 'ASSIGNED', 'WAITING_PAYMENT', 'IN_PROGRESS', 'DELIVERED', 'DISPUTED')"
)


def upgrade() -> None:
    """Build without blocking writes and recover an interrupted concurrent build."""
    with op.get_context().autocommit_block():
        if not context.is_offline_mode():
            valid = op.get_bind().scalar(
                sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"),
                {"name": _INDEX},
            )
            if valid is False:
                op.drop_index(_INDEX, table_name="orders", postgresql_concurrently=True)
        op.create_index(
            _INDEX,
            "orders",
            ["customer_id"],
            postgresql_where=sa.text(_PREDICATE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Remove the additive index without changing order records."""
    with op.get_context().autocommit_block():
        op.drop_index(_INDEX, table_name="orders", postgresql_concurrently=True, if_exists=True)
