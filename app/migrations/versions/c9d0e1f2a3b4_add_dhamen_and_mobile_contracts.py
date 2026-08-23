"""Add dormant Dhamen and mobile persistence contracts.

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add provider-neutral, profile, planning, and attachment persistence."""
    op.execute(sa.text("ALTER TYPE user_status ADD VALUE IF NOT EXISTS 'REJECTED'"))
    op.execute(sa.text("ALTER TYPE withdrawal_status ADD VALUE IF NOT EXISTS 'SUBMITTED'"))
    op.execute(sa.text("ALTER TYPE media_type ADD VALUE IF NOT EXISTS 'PROFILE_AVATAR'"))
    op.execute(sa.text("ALTER TYPE media_type ADD VALUE IF NOT EXISTS 'CHAT_ATTACHMENT'"))
    op.execute(sa.text("ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'MIXED'"))

    op.execute(
        sa.schema.CreateSequence(
            sa.Sequence(
                "gateway_customer_identifier_seq",
                start=100_000_000_000,
                minvalue=100_000_000_000,
                maxvalue=999_999_999_999,
                cycle=False,
            )
        )
    )
    op.add_column("users", sa.Column("avatar_storage_key", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("gateway_customer_identifier", sa.String(12), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE users
            SET gateway_customer_identifier =
                lpad(nextval('gateway_customer_identifier_seq')::text, 12, '0')
            WHERE status = 'ACTIVE' AND gateway_customer_identifier IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM users
                    WHERE status = 'ACTIVE' AND gateway_customer_identifier IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Active user gateway customer identifier backfill incomplete';
                END IF;
            END
            $$
            """
        )
    )
    op.alter_column(
        "users",
        "gateway_customer_identifier",
        server_default=sa.text("lpad(nextval('gateway_customer_identifier_seq')::text, 12, '0')"),
    )
    op.create_check_constraint(
        "chk_users_gateway_customer_identifier",
        "users",
        "gateway_customer_identifier IS NULL OR gateway_customer_identifier ~ '^[0-9]{12}$'",
    )
    op.create_index(
        "uq_users_gateway_customer_identifier",
        "users",
        ["gateway_customer_identifier"],
        unique=True,
        postgresql_where=sa.text("gateway_customer_identifier IS NOT NULL"),
    )

    op.add_column("courier_profiles", sa.Column("gateway_supplier_id", sa.UUID(), nullable=True))
    op.add_column(
        "courier_profiles", sa.Column("payout_iban_encrypted", sa.String(512), nullable=True)
    )
    op.add_column(
        "courier_profiles",
        sa.Column("verification_rejection_reason", sa.String(500), nullable=True),
    )
    op.create_index(
        "uq_courier_profiles_gateway_supplier",
        "courier_profiles",
        ["gateway_supplier_id"],
        unique=True,
        postgresql_where=sa.text("gateway_supplier_id IS NOT NULL"),
    )

    op.add_column("payment_intents", sa.Column("gateway_reference", sa.String(100), nullable=True))
    op.add_column(
        "payment_intents", sa.Column("gateway_payment_url", sa.String(512), nullable=True)
    )
    op.add_column(
        "payment_intents",
        sa.Column("gateway_customer_identifier", sa.String(100), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE payment_intents
            SET gateway_reference = streampay_payment_link_id,
                gateway_payment_url = streampay_payment_url
            WHERE checkout_provider = 'STREAMPAY'
              AND gateway_reference IS NULL
            """
        )
    )
    op.create_index(
        "uq_payment_intents_gateway_reference",
        "payment_intents",
        ["checkout_provider", "gateway_reference"],
        unique=True,
        postgresql_where=sa.text("gateway_reference IS NOT NULL"),
    )

    op.create_table(
        "featured_gifts",
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("subtitle", sa.String(255), nullable=True),
        sa.Column("image_storage_key", sa.String(512), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("price_from_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_from_amount IS NULL OR price_from_amount >= 0",
            name="chk_featured_gifts_price_non_negative",
        ),
        sa.CheckConstraint("display_order >= 0", name="chk_featured_gifts_display_order"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_featured_gifts_active_order",
        "featured_gifts",
        ["is_active", "display_order"],
    )
    op.create_index("idx_featured_gifts_category", "featured_gifts", ["category"])

    op.create_table(
        "occasions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("occasion_date", sa.Date(), nullable=False),
        sa.Column(
            "reminder_days_before", sa.SmallInteger(), server_default=sa.text("7"), nullable=False
        ),
        sa.Column("featured_gift_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reminder_days_before BETWEEN 0 AND 365", name="chk_occasions_reminder_days"
        ),
        sa.ForeignKeyConstraint(
            ["featured_gift_id"],
            ["featured_gifts.id"],
            name="fk_occasions_featured_gift_id_featured_gifts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_occasions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_occasions_user_date", "occasions", ["user_id", "occasion_date"])

    op.create_table(
        "dhamen_notification_receipts",
        sa.Column("notification_id", sa.String(100), nullable=False),
        sa.Column("batch_id", sa.String(100), nullable=False),
        sa.Column("notification_type", sa.String(64), nullable=False),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("transaction_id", sa.String(100), nullable=True),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "processing_outcome", sa.String(32), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("raw_hash ~ '^[0-9a-f]{64}$'", name="chk_dhamen_receipts_raw_hash"),
        sa.CheckConstraint(
            "char_length(processing_outcome) BETWEEN 1 AND 32",
            name="chk_dhamen_receipts_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", name="uq_dhamen_receipts_notification"),
    )
    op.create_index("idx_dhamen_receipts_batch", "dhamen_notification_receipts", ["batch_id"])
    op.create_index(
        "idx_dhamen_receipts_payment_reference",
        "dhamen_notification_receipts",
        ["payment_reference"],
        postgresql_where=sa.text("payment_reference IS NOT NULL"),
    )
    op.create_index(
        "idx_dhamen_receipts_pending",
        "dhamen_notification_receipts",
        ["created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )

    op.create_table(
        "payout_transfers",
        sa.Column("withdrawal_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("payment_reference", sa.String(100), nullable=False),
        sa.Column("supplier_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("uti", sa.String(100), nullable=True),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="chk_payout_transfers_amount_positive"),
        sa.CheckConstraint(
            "char_length(provider) BETWEEN 1 AND 20", name="chk_payout_transfers_provider"
        ),
        sa.CheckConstraint(
            "char_length(status) BETWEEN 1 AND 32", name="chk_payout_transfers_status"
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR submitted_at IS NULL OR completed_at >= submitted_at",
            name="chk_payout_transfers_timestamps",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawal_id"],
            ["withdrawals.id"],
            name="fk_payout_transfers_withdrawal_id_withdrawals",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("withdrawal_id", name="uq_payout_transfers_withdrawal"),
        sa.UniqueConstraint(
            "provider", "payment_reference", name="uq_payout_transfers_provider_reference"
        ),
    )
    op.create_index(
        "idx_payout_transfers_status_submitted",
        "payout_transfers",
        ["status", "submitted_at"],
    )

    op.create_table(
        "message_attachments",
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("display_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png')",
            name="chk_message_attachments_content_type",
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 10485760",
            name="chk_message_attachments_byte_size",
        ),
        sa.CheckConstraint(
            "display_order BETWEEN 0 AND 4", name="chk_message_attachments_display_order"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_message_attachments_message_id_messages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "storage_key", name="uq_message_attachments_message_key"),
    )
    op.create_index(
        "idx_message_attachments_message_order",
        "message_attachments",
        ["message_id", "display_order"],
    )

    op.execute(
        sa.text(
            "CREATE TRIGGER trg_featured_gifts_updated_at BEFORE UPDATE ON featured_gifts "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_occasions_updated_at BEFORE UPDATE ON occasions "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_dhamen_notification_receipts_updated_at "
            "BEFORE UPDATE ON dhamen_notification_receipts FOR EACH ROW "
            "EXECUTE FUNCTION set_updated_at()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_payout_transfers_updated_at BEFORE UPDATE ON payout_transfers "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_message_attachments_updated_at BEFORE UPDATE ON message_attachments "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )


def downgrade() -> None:
    """Remove dormant records while retaining legacy StreamPay and ledger data."""
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM payout_transfers)
                   OR EXISTS (SELECT 1 FROM withdrawals WHERE status = 'SUBMITTED') THEN
                    RAISE EXCEPTION
                        'Refusing downgrade: payout transfer state exists';
                END IF;
            END
            $$
            """
        )
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_message_attachments_updated_at ON message_attachments")
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_payout_transfers_updated_at ON payout_transfers")
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_dhamen_notification_receipts_updated_at "
            "ON dhamen_notification_receipts"
        )
    )
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_occasions_updated_at ON occasions"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_featured_gifts_updated_at ON featured_gifts"))

    op.drop_index("idx_message_attachments_message_order", table_name="message_attachments")
    op.drop_table("message_attachments")
    op.drop_index("idx_payout_transfers_status_submitted", table_name="payout_transfers")
    op.drop_table("payout_transfers")
    op.drop_index("idx_dhamen_receipts_pending", table_name="dhamen_notification_receipts")
    op.drop_index(
        "idx_dhamen_receipts_payment_reference", table_name="dhamen_notification_receipts"
    )
    op.drop_index("idx_dhamen_receipts_batch", table_name="dhamen_notification_receipts")
    op.drop_table("dhamen_notification_receipts")
    op.drop_index("idx_occasions_user_date", table_name="occasions")
    op.drop_table("occasions")
    op.drop_index("idx_featured_gifts_category", table_name="featured_gifts")
    op.drop_index("idx_featured_gifts_active_order", table_name="featured_gifts")
    op.drop_table("featured_gifts")

    op.drop_index("uq_payment_intents_gateway_reference", table_name="payment_intents")
    op.drop_column("payment_intents", "gateway_customer_identifier")
    op.drop_column("payment_intents", "gateway_payment_url")
    op.drop_column("payment_intents", "gateway_reference")

    op.drop_index("uq_courier_profiles_gateway_supplier", table_name="courier_profiles")
    op.drop_column("courier_profiles", "verification_rejection_reason")
    op.drop_column("courier_profiles", "payout_iban_encrypted")
    op.drop_column("courier_profiles", "gateway_supplier_id")

    op.drop_index("uq_users_gateway_customer_identifier", table_name="users")
    op.drop_constraint("chk_users_gateway_customer_identifier", "users", type_="check")
    op.drop_column("users", "gateway_customer_identifier")
    op.drop_column("users", "avatar_storage_key")
    op.execute(sa.schema.DropSequence(sa.Sequence("gateway_customer_identifier_seq")))

    _restore_previous_enums()


def _restore_previous_enums() -> None:
    """Recreate native enums without this revision's dormant values."""
    op.execute(
        sa.text("UPDATE users SET status = 'PENDING_VERIFICATION' WHERE status = 'REJECTED'")
    )
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN status DROP DEFAULT"))
    op.execute(
        sa.text(
            "CREATE TYPE user_status_previous AS ENUM ('ACTIVE','BANNED','PENDING_VERIFICATION')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE users ALTER COLUMN status TYPE user_status_previous "
            "USING status::text::user_status_previous"
        )
    )
    op.execute(sa.text("DROP TYPE user_status"))
    op.execute(sa.text("ALTER TYPE user_status_previous RENAME TO user_status"))
    op.execute(sa.text("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'ACTIVE'::user_status"))

    op.execute(sa.text("UPDATE withdrawals SET status = 'APPROVED' WHERE status = 'SUBMITTED'"))
    op.execute(sa.text("ALTER TABLE withdrawals ALTER COLUMN status DROP DEFAULT"))
    op.execute(
        sa.text(
            "CREATE TYPE withdrawal_status_previous AS ENUM "
            "('REQUESTED','APPROVED','PAID','REJECTED')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE withdrawals ALTER COLUMN status TYPE withdrawal_status_previous "
            "USING status::text::withdrawal_status_previous"
        )
    )
    op.execute(sa.text("DROP TYPE withdrawal_status"))
    op.execute(sa.text("ALTER TYPE withdrawal_status_previous RENAME TO withdrawal_status"))
    op.execute(
        sa.text(
            "ALTER TABLE withdrawals ALTER COLUMN status SET DEFAULT 'REQUESTED'::withdrawal_status"
        )
    )

    op.execute(
        sa.text(
            "UPDATE order_media SET media_type = 'CUSTOMER_REQUEST' "
            "WHERE media_type IN ('PROFILE_AVATAR','CHAT_ATTACHMENT')"
        )
    )
    op.execute(
        sa.text("CREATE TYPE media_type_previous AS ENUM ('CUSTOMER_REQUEST','DELIVERY_PROOF')")
    )
    op.execute(
        sa.text(
            "ALTER TABLE order_media ALTER COLUMN media_type TYPE media_type_previous "
            "USING media_type::text::media_type_previous"
        )
    )
    op.execute(sa.text("DROP TYPE media_type"))
    op.execute(sa.text("ALTER TYPE media_type_previous RENAME TO media_type"))

    op.execute(sa.text("UPDATE messages SET message_type = 'TEXT' WHERE message_type = 'MIXED'"))
    op.execute(
        sa.text("UPDATE courier_portfolios SET media_type = 'IMAGE' WHERE media_type = 'MIXED'")
    )
    op.execute(sa.text("ALTER TABLE messages ALTER COLUMN message_type DROP DEFAULT"))
    op.execute(sa.text("ALTER TABLE courier_portfolios ALTER COLUMN media_type DROP DEFAULT"))
    op.execute(
        sa.text("CREATE TYPE message_type_previous AS ENUM ('TEXT','IMAGE','VIDEO','SYSTEM')")
    )
    op.execute(
        sa.text(
            "ALTER TABLE messages ALTER COLUMN message_type TYPE message_type_previous "
            "USING message_type::text::message_type_previous"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE courier_portfolios ALTER COLUMN media_type TYPE message_type_previous "
            "USING media_type::text::message_type_previous"
        )
    )
    op.execute(sa.text("DROP TYPE message_type"))
    op.execute(sa.text("ALTER TYPE message_type_previous RENAME TO message_type"))
    op.execute(
        sa.text("ALTER TABLE messages ALTER COLUMN message_type SET DEFAULT 'TEXT'::message_type")
    )
    op.execute(
        sa.text(
            "ALTER TABLE courier_portfolios ALTER COLUMN media_type SET DEFAULT 'IMAGE'::message_type"
        )
    )
