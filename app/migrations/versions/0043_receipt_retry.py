"""Persist due times and exhausted state for paid-invoice receipt delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0043_receipt_retry"
down_revision = "0042_active_order_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep sent timestamps and in-flight claims while enabling scheduled retries."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.add_column("invoices", sa.Column("receipt_first_attempt_at", sa.DateTime(timezone=True)))
    op.add_column(
        "invoices",
        sa.Column("receipt_failed_attempts", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("invoices", sa.Column("receipt_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("receipt_exhausted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    """Remove retry metadata while preserving the original claims and sent timestamps."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.drop_column("invoices", "receipt_exhausted_at")
    op.drop_column("invoices", "receipt_next_attempt_at")
    op.drop_column("invoices", "receipt_failed_attempts")
    op.drop_column("invoices", "receipt_first_attempt_at")
