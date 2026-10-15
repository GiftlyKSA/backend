"""Index measured global admin financial chronology without altering ledger data."""

import sqlalchemy as sa
from alembic import op

revision = "0036_admin_financial_chronology"
down_revision = "0035_reward_transaction_types"
branch_labels = None
depends_on = None
_TABLES = ("invoices", "transactions", "wallet_topups")


def upgrade() -> None:
    """Bound write-blocking index builds; failure rolls back the entire revision."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    for table in _TABLES:
        op.create_index(
            f"idx_{table}_created", table, [sa.text("created_at DESC"), sa.text("id DESC")]
        )


def downgrade() -> None:
    """Remove only these optional read indexes; financial rows are unchanged."""
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    for table in reversed(_TABLES):
        op.drop_index(f"idx_{table}_created", table_name=table)
