"""Record proof of ownership for the currently stored participant phone."""

import sqlalchemy as sa
from alembic import op

revision = "0046_verified_participant_phone"
down_revision = "0045_receipt_due_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Leave legacy rows unverified and clear proof whenever the phone changes."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "users",
        sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("""
        CREATE FUNCTION giftly_clear_phone_verification() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.phone_verified := false;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER giftly_clear_phone_verification
        BEFORE UPDATE OF phone ON users FOR EACH ROW
        WHEN (OLD.phone IS DISTINCT FROM NEW.phone)
        EXECUTE FUNCTION giftly_clear_phone_verification()
    """)


def downgrade() -> None:
    """Remove disclosure evidence without modifying account phone numbers."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TRIGGER giftly_clear_phone_verification ON users")
    op.execute("DROP FUNCTION giftly_clear_phone_verification()")
    op.drop_column("users", "phone_verified")
