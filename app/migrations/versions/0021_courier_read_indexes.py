"""Cover courier radar and deterministic actor/revision keyset ordering.

Index replacement is transactional and blocks writes to the affected tables while
building. Schedule migration during a maintenance window on large installations.
Downgrade restores the previous index definitions without modifying business data.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_courier_read_indexes"
down_revision = "0018_remove_identity_fingerprint"
branch_labels = None
depends_on = None


def _replace_indexes(*, include_tiebreaker: bool) -> None:
    for name, table, owner, timestamp, predicate in (
        ("idx_orders_customer_created", "orders", "customer_id", "created_at", None),
        (
            "idx_orders_courier_created",
            "orders",
            "courier_id",
            "created_at",
            "courier_id IS NOT NULL",
        ),
        ("idx_invoices_order", "invoices", "order_id", None, None),
        (
            "idx_conversations_customer_inbox",
            "conversations",
            "customer_id",
            "last_message_timestamp",
            None,
        ),
        (
            "idx_conversations_courier_inbox",
            "conversations",
            "courier_id",
            "last_message_timestamp",
            None,
        ),
    ):
        columns: list[str | sa.TextClause] = [owner]
        if timestamp is not None:
            columns.append(sa.text(f"{timestamp} DESC"))
        elif include_tiebreaker:
            columns.append(sa.text("created_at DESC"))
        if include_tiebreaker:
            columns.append(sa.text("id DESC"))
        op.drop_index(name, table_name=table)
        op.create_index(
            name,
            table,
            columns,
            postgresql_where=sa.text(predicate) if predicate is not None else None,
        )


def upgrade() -> None:
    """Extend existing indexes and add a bounded NEW-order radar index."""
    _replace_indexes(include_tiebreaker=True)
    op.create_index(
        "idx_orders_radar_keyset",
        "orders",
        ["delivery_city_id", sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("status = 'NEW'"),
    )


def downgrade() -> None:
    """Restore previous index shapes and remove radar coverage."""
    op.drop_index("idx_orders_radar_keyset", table_name="orders")
    _replace_indexes(include_tiebreaker=False)
