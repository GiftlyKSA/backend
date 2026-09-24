"""Record wallet reservation ownership on each payment attempt.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_intents",
        sa.Column("wallet_reserved_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    # Historical failed holds may have been overwritten by retries. Never infer them.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM payment_intents p
                JOIN invoices i ON i.id = p.reference_invoice_id
                WHERE p.status = 'NEW' AND (
                    i.status <> 'ISSUED' OR p.amount <> i.amount_from_gateway
                    OR p.amount + i.amount_from_wallet <> i.total_amount
                    OR (SELECT count(*) FROM payment_intents other
                        WHERE other.reference_invoice_id = p.reference_invoice_id
                        AND other.status = 'NEW') <> 1
                )
            ) THEN
                RAISE EXCEPTION 'Ambiguous active payment reservations require reconciliation';
            END IF;
        END $$;
    """)
    op.execute("""
        UPDATE payment_intents p SET wallet_reserved_amount = i.amount_from_wallet
        FROM invoices i
        WHERE p.reference_invoice_id = i.id AND p.status = 'NEW'
    """)
    op.create_check_constraint(
        "chk_intent_reservation_nonnegative", "payment_intents", "wallet_reserved_amount >= 0"
    )
    op.create_check_constraint(
        "chk_intent_reservation_purpose",
        "payment_intents",
        "purpose='ORDER_INVOICE' OR wallet_reserved_amount=0",
    )


def downgrade() -> None:
    op.drop_constraint("chk_intent_reservation_purpose", "payment_intents", type_="check")
    op.drop_constraint("chk_intent_reservation_nonnegative", "payment_intents", type_="check")
    op.drop_column("payment_intents", "wallet_reserved_amount")
