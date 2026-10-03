"""Retain transaction-scoped customer invoice-promo operation receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_invoice_promo_operations"
down_revision = "0011_restore_address_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create durable idempotency receipts without rewriting historical invoices."""
    op.create_table(
        "invoice_promo_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=True),
        sa.Column(
            "result_invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "customer_id", "idempotency_key", name="uq_invoice_promo_operation_key"
        ),
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER trg_giftly_audit_invoice_promo_operations
        AFTER INSERT OR UPDATE OR DELETE ON invoice_promo_operations
        FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('id')
    """)
    )


def downgrade() -> None:
    """Remove receipts; existing invoice revisions and reservations remain intact."""
    op.drop_table("invoice_promo_operations")
