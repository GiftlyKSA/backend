"""Add optional gender and an explicit deleted-account lifecycle."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_user_lifecycle"
down_revision = "0016_notification_recipients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve existing accounts and enforce deletion metadata at the database boundary."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE user_status ADD VALUE IF NOT EXISTS 'DELETED'")
    gender = postgresql.ENUM("MALE", "FEMALE", "OTHER", "PREFER_NOT_TO_SAY", name="user_gender")
    gender.create(op.get_bind(), checkfirst=True)
    op.add_column("users", sa.Column("gender", gender, nullable=True))
    op.add_column("users", sa.Column("deletion_reason", sa.String(500), nullable=True))
    op.execute("""
        CREATE FUNCTION giftly_user_deletion_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status = 'DELETED' THEN
            NEW.deleted_at := COALESCE(NEW.deleted_at, now());
            NEW.deletion_reason := COALESCE(NULLIF(btrim(NEW.deletion_reason), ''),
                                           'Deleted by an administrator.');
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_giftly_user_deletion_metadata
        BEFORE INSERT OR UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION giftly_user_deletion_metadata()
    """)
    op.execute("UPDATE users SET status = 'DELETED' WHERE deleted_at IS NOT NULL")


def downgrade() -> None:
    """Refuse data loss when the new profile metadata has been populated."""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM users WHERE gender IS NOT NULL OR deletion_reason IS NOT NULL)
          THEN RAISE EXCEPTION 'Export user lifecycle metadata before downgrade'; END IF;
        END $$
    """)
    op.execute("DROP TRIGGER trg_giftly_user_deletion_metadata ON users")
    op.execute("DROP FUNCTION giftly_user_deletion_metadata()")
    op.execute("UPDATE users SET status = 'BANNED' WHERE status = 'DELETED'")
    op.drop_column("users", "deletion_reason")
    op.drop_column("users", "gender")
    postgresql.ENUM(name="user_gender").drop(op.get_bind(), checkfirst=True)
