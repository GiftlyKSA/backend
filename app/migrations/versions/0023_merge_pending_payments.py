"""Merge independent payment and courier performance migration branches."""

revision = "0023_merge_pending_payments"
down_revision = ("0020_pending_payment_ledger", "0022_chat_notifications")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join branches without changing data."""


def downgrade() -> None:
    """Restore independent branches without changing data."""
