"""Build the receipt ready-queue index without blocking invoice writes."""

import sqlalchemy as sa
from alembic import context, op

revision = "0045_receipt_due_index"
down_revision = "0044_reminder_owner_fence"
branch_labels = None
depends_on = None

_DUE_INDEX = "idx_invoices_receipt_due"
_LEGACY_INDEX = "idx_invoices_receipt_pending"


def _remove_invalid(name: str) -> None:
    if context.is_offline_mode():
        return
    valid = op.get_bind().scalar(
        sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"),
        {"name": name},
    )
    if valid is False:
        op.drop_index(name, table_name="invoices", postgresql_concurrently=True)


def upgrade() -> None:
    """Keep the original index until the ordered ready-queue index is valid."""
    with op.get_context().autocommit_block():
        _remove_invalid(_DUE_INDEX)
        op.create_index(
            _DUE_INDEX,
            "invoices",
            [sa.text("coalesce(receipt_next_attempt_at, paid_at, created_at)"), "id"],
            postgresql_where=sa.text(
                "status='PAID' AND receipt_email_sent_at IS NULL AND receipt_exhausted_at IS NULL"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            _LEGACY_INDEX, table_name="invoices", postgresql_concurrently=True, if_exists=True
        )


def downgrade() -> None:
    """Restore the original index before removing ready-queue coverage."""
    with op.get_context().autocommit_block():
        _remove_invalid(_LEGACY_INDEX)
        op.create_index(
            _LEGACY_INDEX,
            "invoices",
            ["id"],
            postgresql_where=sa.text("status='PAID' AND receipt_email_sent_at IS NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            _DUE_INDEX, table_name="invoices", postgresql_concurrently=True, if_exists=True
        )
