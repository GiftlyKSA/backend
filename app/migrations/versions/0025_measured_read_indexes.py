"""Add measured calendar-day and owned payment recovery indexes concurrently."""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import TextClause

revision = "0025_measured_read_indexes"
down_revision = "0024_remove_invoice_vat"
branch_labels = None
depends_on = None

_INDEXES: tuple[tuple[str, str, list[str | TextClause], str | None], ...] = (
    (
        "idx_orders_customer_calendar",
        "orders",
        ["customer_id", "delivery_date", sa.text("created_at DESC"), sa.text("id DESC")],
        None,
    ),
    (
        "idx_orders_courier_calendar",
        "orders",
        ["courier_id", "delivery_date", sa.text("created_at DESC"), sa.text("id DESC")],
        "courier_id IS NOT NULL",
    ),
    (
        "idx_payment_intents_topup_recovery",
        "payment_intents",
        [
            "user_id",
            sa.text("(checkout_state = 'REVIEW') DESC"),
            sa.text("(status = 'NEW') DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        "purpose='WALLET_TOPUP' AND checkout_provider!='SIMULATED'",
    ),
    (
        "idx_payment_intents_open_topup_recovery",
        "payment_intents",
        [
            "user_id",
            sa.text("(checkout_state = 'REVIEW') DESC"),
            sa.text("(status = 'NEW') DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        "purpose='WALLET_TOPUP' AND checkout_provider!='SIMULATED' AND (status='NEW' OR checkout_state='REVIEW')",
    ),
    (
        "idx_payment_intents_order_recovery",
        "payment_intents",
        [
            "order_id",
            "user_id",
            sa.text("(checkout_state = 'REVIEW') DESC"),
            sa.text("(status = 'NEW') DESC"),
            sa.text("created_at DESC"),
            sa.text("id DESC"),
        ],
        "order_id IS NOT NULL",
    ),
)


def upgrade() -> None:
    """Avoid blocking writes; recover invalid indexes left by interrupted builds."""
    with op.get_context().autocommit_block():
        for name, table, columns, predicate in _INDEXES:
            if not context.is_offline_mode():
                valid = op.get_bind().scalar(
                    sa.text(
                        "SELECT i.indisvalid FROM pg_index i "
                        "WHERE i.indexrelid = to_regclass(:name)"
                    ),
                    {"name": name},
                )
                if valid is False:
                    op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(
                name,
                table,
                columns,
                postgresql_where=sa.text(predicate) if predicate else None,
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    """Remove only the five additive indexes without modifying financial data."""
    with op.get_context().autocommit_block():
        for name, table, _columns, _predicate in reversed(_INDEXES):
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
