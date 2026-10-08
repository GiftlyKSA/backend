"""Permit verified pending settlement and coordinate hosted wallet top-ups."""

import sqlalchemy as sa
from alembic import op

revision = "0020_pending_payment_ledger"
down_revision = "0019_hosted_payment_sessions"
branch_labels = None
depends_on = None


def _guard_sql(*, settle_balance: bool) -> str:
    permitted = " - 'balance_after'" if settle_balance else ""
    return f"""
    CREATE OR REPLACE FUNCTION giftly_guard_transaction()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF giftly_admin_maintenance_allowed() THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END IF;
      IF TG_OP = 'UPDATE' AND OLD.status = 'PENDING'
         AND NEW.status IN ('SETTLED', 'REVERSED')
         AND to_jsonb(NEW) - 'status' - 'updated_at'
             = to_jsonb(OLD) - 'status' - 'updated_at' THEN
        RETURN NEW;
      END IF;
      IF TG_OP = 'UPDATE' AND OLD.status = 'PENDING' AND NEW.status = 'SETTLED'
         AND to_jsonb(NEW) - 'status' - 'updated_at'{permitted}
             = to_jsonb(OLD) - 'status' - 'updated_at'{permitted} THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'Ledger entries are immutable';
    END $$
    """


def upgrade() -> None:
    """Keep immutable amounts/references; allow settlement to store its actual balance."""
    op.execute(_guard_sql(settle_balance=True))
    op.add_column(
        "payment_intents",
        sa.Column("use_wallet", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute("UPDATE payment_intents SET use_wallet=false WHERE purpose='WALLET_TOPUP'")
    op.create_index(
        "uq_payment_intents_open_topup",
        "payment_intents",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "purpose='WALLET_TOPUP' AND status='NEW' AND checkout_provider!='SIMULATED'"
        ),
    )
    op.create_index(
        "idx_transactions_intent",
        "transactions",
        ["reference_intent_id"],
        postgresql_where=sa.text("reference_intent_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Refuse rollback while pending or reviewed financial operations need this code."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT id FROM transactions WHERE status='PENDING' "
        "AND reference_intent_id IS NOT NULL) OR EXISTS (SELECT id FROM payment_intents "
        "WHERE checkout_provider!='SIMULATED' AND (status='NEW' OR checkout_state='REVIEW')) "
        "THEN RAISE EXCEPTION 'Resolve pending payment ledger groups before downgrade.'; "
        "END IF; END $$"
    )
    op.drop_index("idx_transactions_intent", table_name="transactions")
    op.drop_index("uq_payment_intents_open_topup", table_name="payment_intents")
    op.drop_column("payment_intents", "use_wallet")
    op.execute(_guard_sql(settle_balance=False))
