"""Add durable claims for receipt delivery."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_receipt_claims"
down_revision = "0004_media_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Track the current receipt worker and its lease on each invoice."""
    op.add_column(
        "invoices", sa.Column("receipt_claim_token", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "invoices", sa.Column("receipt_claimed_until", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Remove claims; pending receipts remain pending after rollback."""
    op.drop_column("invoices", "receipt_claimed_until")
    op.drop_column("invoices", "receipt_claim_token")
