"""Persist checkout creation and enforce one open payment attempt per order."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_hosted_payment_sessions"
down_revision = "0018_remove_identity_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill invoice orders; duplicate attempts require reconciliation before upgrade."""
    op.add_column("payment_intents", sa.Column("order_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_payment_intents_order_id", "payment_intents", "orders", ["order_id"], ["id"]
    )
    op.add_column(
        "payment_intents",
        sa.Column("checkout_state", sa.String(20), nullable=False, server_default="ACTIVE"),
    )
    op.add_column("payment_intents", sa.Column("checkout_snapshot", postgresql.JSONB()))
    op.add_column("payment_intents", sa.Column("checkout_checked_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE payment_intents p SET order_id=i.order_id FROM invoices i WHERE p.reference_invoice_id=i.id"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT order_id FROM payment_intents WHERE status='NEW' AND order_id IS NOT NULL "
        "GROUP BY order_id HAVING count(*)>1"
        ") THEN RAISE EXCEPTION 'Reconcile duplicate open order payments before migration 0019.'; "
        "END IF; END $$"
    )
    op.create_index(
        "uq_payment_intents_open_order",
        "payment_intents",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status='NEW' AND order_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "chk_intent_checkout_state",
        "payment_intents",
        "checkout_state IN ('CREATING','ACTIVE','CLOSING','CLOSED','REVIEW')",
    )


def downgrade() -> None:
    """Remove session coordination only after all real gateway attempts are terminal."""
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT id FROM payment_intents WHERE checkout_provider!='SIMULATED' "
        "AND (status='NEW' OR checkout_state='REVIEW')"
        ") THEN RAISE EXCEPTION 'Close or settle real provider sessions before downgrading 0019.'; "
        "END IF; END $$"
    )
    op.drop_constraint("chk_intent_checkout_state", "payment_intents", type_="check")
    op.drop_index("uq_payment_intents_open_order", table_name="payment_intents")
    op.drop_column("payment_intents", "checkout_snapshot")
    op.drop_column("payment_intents", "checkout_checked_at")
    op.drop_column("payment_intents", "checkout_state")
    op.drop_constraint("fk_payment_intents_order_id", "payment_intents", type_="foreignkey")
    op.drop_column("payment_intents", "order_id")
