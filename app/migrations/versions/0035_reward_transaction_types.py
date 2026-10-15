"""Add pending reward transaction labels without rewriting applied migration history."""

from alembic import op

revision = "0035_reward_transaction_types"
down_revision = "0034_expo_push_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend the existing enum; no reward operation is exposed by this migration."""
    op.execute("ALTER TYPE transaction_type ADD VALUE IF NOT EXISTS 'GIVEAWAY'")
    op.execute("ALTER TYPE transaction_type ADD VALUE IF NOT EXISTS 'PROMO_CREDIT'")


def downgrade() -> None:
    """Refuse incompatible history; retain unused PostgreSQL enum labels safely."""
    op.execute("LOCK TABLE transactions IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM transactions "
        "WHERE type::text IN ('GIVEAWAY','PROMO_CREDIT')) "
        "THEN RAISE EXCEPTION 'Retain reward transaction support while history exists'; "
        "END IF; END $$"
    )
