"""Create the initial Giftly PostgreSQL schema and required seed rows."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:  # noqa: C901 - generated baseline enumerates each frozen schema object
    """Create missing schema objects and seed required reference rows."""
    offline = op.get_context().as_sql
    if offline:
        bind = None
        existing_tables: set[str] = set()
        existing_indexes: dict[str, set[str]] = {}
    else:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
        existing_indexes = {
            table_name: {
                index["name"]
                for index in inspector.get_indexes(table_name)
                if index["name"] is not None
            }
            for table_name in existing_tables
        }

    op.execute(
        sa.text("CREATE SEQUENCE IF NOT EXISTS gateway_customer_identifier_seq START WITH 1")
    )
    if "cities" not in existing_tables:
        op.create_table(
            "cities",
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("shortcut", sa.String(length=16), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
            sa.UniqueConstraint("shortcut"),
        )
        existing_tables.add("cities")

    if "dhamen_notification_receipts" not in existing_tables:
        op.create_table(
            "dhamen_notification_receipts",
            sa.Column("notification_id", sa.String(length=100), nullable=False),
            sa.Column("batch_id", sa.String(length=100), nullable=False),
            sa.Column("notification_type", sa.String(length=64), nullable=False),
            sa.Column("payment_reference", sa.String(length=100), nullable=True),
            sa.Column("transaction_id", sa.String(length=100), nullable=True),
            sa.Column("raw_hash", sa.String(length=64), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "processing_outcome",
                sa.String(length=32),
                server_default=sa.text("'PENDING'"),
                nullable=False,
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
        existing_tables.add("dhamen_notification_receipts")

    if "featured_gifts" not in existing_tables:
        op.create_table(
            "featured_gifts",
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("subtitle", sa.String(length=255), nullable=True),
            sa.Column("image_storage_key", sa.String(length=512), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("price_from_amount", sa.Numeric(precision=12, scale=2), nullable=True),
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
            sa.CheckConstraint("display_order >= 0", name="chk_featured_gifts_display_order"),
            sa.CheckConstraint(
                "price_from_amount IS NULL OR price_from_amount >= 0",
                name="chk_featured_gifts_price_non_negative",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("featured_gifts")

    if "otp_attempts" not in existing_tables:
        op.create_table(
            "otp_attempts",
            sa.Column("phone_hash", sa.String(length=64), nullable=False),
            sa.Column("ip_address", postgresql.INET(), nullable=True),
            sa.Column("was_successful", sa.Boolean(), nullable=False),
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
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("otp_attempts")

    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("phone", sa.String(length=20), nullable=False),
            sa.Column("auth_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("full_name", sa.String(length=120), nullable=True),
            sa.Column("date_of_birth", sa.Date(), nullable=True),
            sa.Column(
                "role",
                postgresql.ENUM("CUSTOMER", "COURIER", "ADMIN", name="user_role"),
                nullable=False,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "ACTIVE", "BANNED", "PENDING_VERIFICATION", "REJECTED", name="user_status"
                ),
                server_default="ACTIVE",
                nullable=False,
            ),
            sa.Column(
                "rating",
                sa.Numeric(precision=2, scale=1),
                server_default=sa.text("5.0"),
                nullable=False,
            ),
            sa.Column("rating_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("avatar_storage_key", sa.String(length=512), nullable=True),
            sa.Column(
                "gateway_customer_identifier",
                sa.String(length=12),
                server_default=sa.text(
                    "lpad(nextval('gateway_customer_identifier_seq')::text, 12, '0')"
                ),
                nullable=True,
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
                "gateway_customer_identifier IS NULL OR gateway_customer_identifier ~ '^[0-9]{12}$'",
                name="chk_users_gateway_customer_identifier",
            ),
            sa.CheckConstraint("rating BETWEEN 0.0 AND 5.0", name="chk_rating_range"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("phone", name="uq_users_phone"),
        )
        existing_tables.add("users")

    if "admin_sessions" not in existing_tables:
        op.create_table(
            "admin_sessions",
            sa.Column("admin_user_id", sa.UUID(), nullable=False),
            sa.Column("session_token_hash", sa.String(length=64), nullable=False),
            sa.Column("ip_address", postgresql.INET(), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_token_hash", name="uq_admin_sessions_token_hash"),
        )
        existing_tables.add("admin_sessions")

    if "audit_logs" not in existing_tables:
        op.create_table(
            "audit_logs",
            sa.Column("actor_user_id", sa.UUID(), nullable=True),
            sa.Column("action", sa.String(length=100), nullable=False),
            sa.Column("entity_type", sa.String(length=50), nullable=False),
            sa.Column("entity_id", sa.UUID(), nullable=True),
            sa.Column("ip_address", postgresql.INET(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            sa.ForeignKeyConstraint(
                ["actor_user_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("audit_logs")

    if "courier_portfolios" not in existing_tables:
        op.create_table(
            "courier_portfolios",
            sa.Column("courier_id", sa.UUID(), nullable=False),
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column(
                "media_type",
                postgresql.ENUM("TEXT", "IMAGE", "VIDEO", "SYSTEM", "MIXED", name="message_type"),
                server_default="IMAGE",
                nullable=False,
            ),
            sa.Column("description", sa.String(length=500), nullable=True),
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
            sa.ForeignKeyConstraint(["courier_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("courier_portfolios")

    if "courier_profiles" not in existing_tables:
        op.create_table(
            "courier_profiles",
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("passport_id_encrypted", sa.String(length=512), nullable=True),
            sa.Column("national_id_encrypted", sa.String(length=512), nullable=True),
            sa.Column("identity_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("city_of_residence_id", sa.UUID(), nullable=False),
            sa.Column("bio", sa.Text(), nullable=True),
            sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_by_admin_id", sa.UUID(), nullable=True),
            sa.Column("gateway_supplier_id", sa.UUID(), nullable=True),
            sa.Column("payout_iban_encrypted", sa.String(length=512), nullable=True),
            sa.Column("verification_rejection_reason", sa.String(length=500), nullable=True),
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
                "passport_id_encrypted IS NOT NULL OR national_id_encrypted IS NOT NULL",
                name="chk_identity_present",
            ),
            sa.ForeignKeyConstraint(
                ["city_of_residence_id"],
                ["cities.id"],
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["verified_by_admin_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("user_id"),
        )
        existing_tables.add("courier_profiles")

    if "device_tokens" not in existing_tables:
        op.create_table(
            "device_tokens",
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column(
                "device_os", postgresql.ENUM("IOS", "ANDROID", name="device_os"), nullable=False
            ),
            sa.Column("token", sa.String(length=512), nullable=False),
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
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
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token", name="uq_device_tokens_token"),
        )
        existing_tables.add("device_tokens")

    if "media_uploads" not in existing_tables:
        op.create_table(
            "media_uploads",
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column("owner_user_id", sa.UUID(), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("content_type", sa.String(length=32), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.CheckConstraint(
                "content_type IN ('image/jpeg', 'image/png')", name="chk_media_content_type"
            ),
            sa.CheckConstraint(
                "purpose IN ('ORDER_REQUEST', 'DELIVERY_PROOF')", name="chk_media_purpose"
            ),
            sa.CheckConstraint("byte_size > 0", name="chk_media_byte_size_positive"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("storage_key", name="uq_media_uploads_storage_key"),
        )
        existing_tables.add("media_uploads")

    if "occasions" not in existing_tables:
        op.create_table(
            "occasions",
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("occasion_date", sa.Date(), nullable=False),
            sa.Column(
                "reminder_days_before",
                sa.SmallInteger(),
                server_default=sa.text("7"),
                nullable=False,
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
                ["featured_gift_id"], ["featured_gifts.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("occasions")

    if "orders" not in existing_tables:
        op.create_table(
            "orders",
            sa.Column("customer_id", sa.UUID(), nullable=False),
            sa.Column("courier_id", sa.UUID(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("delivery_city_id", sa.UUID(), nullable=False),
            sa.Column("delivery_map_url", sa.String(length=2048), nullable=False),
            sa.Column("delivery_address_note", sa.String(length=255), nullable=True),
            sa.Column("delivery_date", sa.Date(), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "NEW",
                    "ASSIGNED",
                    "WAITING_PAYMENT",
                    "IN_PROGRESS",
                    "DELIVERED",
                    "COMPLETED",
                    "CANCELLED",
                    "DISPUTED",
                    "REFUNDED",
                    name="order_status",
                ),
                server_default="NEW",
                nullable=False,
            ),
            sa.Column(
                "total_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "commission_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "courier_payout_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_reason", sa.String(length=255), nullable=True),
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
                "delivery_date >= CURRENT_DATE AND delivery_date <= CURRENT_DATE + INTERVAL '180 days'",
                name="chk_delivery_date_window",
            ),
            sa.CheckConstraint(
                "status IN ('NEW','CANCELLED') OR courier_id IS NOT NULL",
                name="chk_courier_required_after_assignment",
            ),
            sa.CheckConstraint(
                "total_amount >= 0 AND commission_amount >= 0 AND courier_payout_amount >= 0",
                name="chk_amounts_non_negative",
            ),
            sa.ForeignKeyConstraint(["courier_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(
                ["delivery_city_id"],
                ["cities.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("orders")

    if "promos" not in existing_tables:
        op.create_table(
            "promos",
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=False),
            sa.Column(
                "discount_type",
                postgresql.ENUM("PERCENT", "FIXED", name="promo_discount_type"),
                nullable=False,
            ),
            sa.Column("percent_value", sa.Numeric(precision=5, scale=2), nullable=True),
            sa.Column("fixed_amount", sa.Numeric(precision=12, scale=2), nullable=True),
            sa.Column("max_discount_amount", sa.Numeric(precision=12, scale=2), nullable=True),
            sa.Column(
                "min_order_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column("max_total_usages", sa.Integer(), nullable=True),
            sa.Column(
                "max_usages_per_user", sa.Integer(), server_default=sa.text("1"), nullable=False
            ),
            sa.Column("used_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by_admin_id", sa.UUID(), nullable=True),
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
                "(discount_type='PERCENT' AND percent_value IS NOT NULL AND fixed_amount IS NULL AND percent_value > 0 AND percent_value <= 100) OR (discount_type='FIXED' AND fixed_amount IS NOT NULL AND percent_value IS NULL AND fixed_amount > 0)",
                name="chk_promo_value_by_type",
            ),
            sa.CheckConstraint("char_length(code) BETWEEN 3 AND 32", name="chk_promo_code_len"),
            sa.CheckConstraint(
                "code = upper(code) AND code = btrim(code)", name="chk_promo_code_upper"
            ),
            sa.CheckConstraint(
                "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
                name="chk_promo_window",
            ),
            sa.CheckConstraint(
                "used_count >= 0 AND (max_total_usages IS NULL OR used_count <= max_total_usages) AND max_usages_per_user >= 1",
                name="chk_promo_usage_bounds",
            ),
            sa.ForeignKeyConstraint(
                ["created_by_admin_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_promos_code"),
        )
        existing_tables.add("promos")

    if "refresh_tokens" not in existing_tables:
        op.create_table(
            "refresh_tokens",
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("family_id", sa.UUID(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_hash"),
        )
        existing_tables.add("refresh_tokens")

    if "wallets" not in existing_tables:
        op.create_table(
            "wallets",
            sa.Column("user_id", sa.UUID(), nullable=True),
            sa.Column(
                "type",
                postgresql.ENUM(
                    "CUSTOMER",
                    "COURIER",
                    "SYSTEM_ESCROW",
                    "SYSTEM_REVENUE",
                    "SYSTEM_GATEWAY",
                    "SYSTEM_TAX_PAYABLE",
                    name="wallet_type",
                ),
                nullable=False,
            ),
            sa.Column(
                "balance",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "held_balance",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "currency", sa.String(length=3), server_default=sa.text("'SAR'"), nullable=False
            ),
            sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
                "(type IN ('CUSTOMER','COURIER') AND user_id IS NOT NULL) OR (type IN ('SYSTEM_ESCROW','SYSTEM_REVENUE','SYSTEM_GATEWAY','SYSTEM_TAX_PAYABLE') AND user_id IS NULL)",
                name="chk_user_wallet_pairing",
            ),
            sa.CheckConstraint(
                "type IN ('SYSTEM_GATEWAY','SYSTEM_REVENUE') OR (balance >= 0 AND held_balance >= 0)",
                name="chk_balance_non_negative",
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("wallets")

    if "conversations" not in existing_tables:
        op.create_table(
            "conversations",
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("customer_id", sa.UUID(), nullable=False),
            sa.Column("courier_id", sa.UUID(), nullable=False),
            sa.Column("last_message_preview_encrypted", sa.String(length=512), nullable=True),
            sa.Column(
                "last_message_timestamp",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "customer_unread_count", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
            sa.Column(
                "courier_unread_count", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
            sa.Column("is_closed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["courier_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("order_id", name="uq_conversations_order"),
        )
        existing_tables.add("conversations")

    if "disputes" not in existing_tables:
        op.create_table(
            "disputes",
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("raised_by_user_id", sa.UUID(), nullable=False),
            sa.Column("reason", sa.String(length=1000), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "OPEN",
                    "RESOLVED_CUSTOMER",
                    "RESOLVED_COURIER",
                    "RESOLVED_SPLIT",
                    name="dispute_status",
                ),
                server_default="OPEN",
                nullable=False,
            ),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("resolved_by_admin_id", sa.UUID(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(
                ["order_id"],
                ["orders.id"],
            ),
            sa.ForeignKeyConstraint(
                ["raised_by_user_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["resolved_by_admin_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("order_id", name="uq_disputes_order"),
        )
        existing_tables.add("disputes")

    if "invoices" not in existing_tables:
        op.create_table(
            "invoices",
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("issued_by_courier_id", sa.UUID(), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "DRAFT",
                    "ISSUED",
                    "PAID",
                    "CANCELLED",
                    "EXPIRED",
                    "REFUNDED",
                    name="invoice_status",
                ),
                server_default="DRAFT",
                nullable=False,
            ),
            sa.Column(
                "currency", sa.String(length=3), server_default=sa.text("'SAR'"), nullable=False
            ),
            sa.Column(
                "items_net_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "courier_fee_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "service_fee_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "discount_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "net_after_discount_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "tax_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "total_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column("promo_id", sa.UUID(), nullable=True),
            sa.Column("promo_code_snapshot", sa.String(length=32), nullable=True),
            sa.Column(
                "payment_method",
                postgresql.ENUM("WALLET_ONLY", "GATEWAY_ONLY", "SPLIT", name="payment_method"),
                nullable=True,
            ),
            sa.Column(
                "amount_from_wallet",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column(
                "amount_from_gateway",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column("pricing_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("receipt_email_sent_at", sa.DateTime(timezone=True), nullable=True),
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
                "status = 'DRAFT' OR total_amount > 0", name="chk_invoice_total_positive"
            ),
            sa.CheckConstraint(
                "(promo_id IS NULL AND discount_amount = 0) OR (promo_id IS NOT NULL AND discount_amount > 0)",
                name="chk_invoice_promo_pairing",
            ),
            sa.CheckConstraint(
                "items_net_amount >= 0 AND courier_fee_amount >= 0 AND service_fee_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 AND total_amount >= 0",
                name="chk_invoice_amounts_non_negative",
            ),
            sa.CheckConstraint(
                "net_after_discount_amount = items_net_amount + courier_fee_amount + service_fee_amount - discount_amount",
                name="chk_invoice_net_math",
            ),
            sa.CheckConstraint(
                "total_amount = net_after_discount_amount + tax_amount",
                name="chk_invoice_total_math",
            ),
            sa.ForeignKeyConstraint(
                ["issued_by_courier_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["promo_id"], ["promos.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("invoices")

    if "order_media" not in existing_tables:
        op.create_table(
            "order_media",
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("uploaded_by_user_id", sa.UUID(), nullable=False),
            sa.Column(
                "media_type",
                postgresql.ENUM(
                    "CUSTOMER_REQUEST",
                    "DELIVERY_PROOF",
                    "PROFILE_AVATAR",
                    "CHAT_ATTACHMENT",
                    name="media_type",
                ),
                nullable=False,
            ),
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column("content_type", sa.String(length=50), nullable=False),
            sa.Column("byte_size", sa.BigInteger(), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["uploaded_by_user_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("order_media")

    if "ratings" not in existing_tables:
        op.create_table(
            "ratings",
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("rater_id", sa.UUID(), nullable=False),
            sa.Column("rated_user_id", sa.UUID(), nullable=False),
            sa.Column("score", sa.SmallInteger(), nullable=False),
            sa.Column("comment", sa.String(length=500), nullable=True),
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
            sa.CheckConstraint("rater_id <> rated_user_id", name="chk_no_self_rating"),
            sa.CheckConstraint("score BETWEEN 1 AND 5", name="chk_score_range"),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["rated_user_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["rater_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("order_id", "rater_id", name="uq_ratings_order_rater"),
        )
        existing_tables.add("ratings")

    if "withdrawals" not in existing_tables:
        op.create_table(
            "withdrawals",
            sa.Column("courier_id", sa.UUID(), nullable=False),
            sa.Column("wallet_id", sa.UUID(), nullable=False),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("iban_encrypted", sa.String(length=512), nullable=False),
            sa.Column("iban_last4", sa.String(length=4), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "REQUESTED",
                    "APPROVED",
                    "SUBMITTED",
                    "PAID",
                    "REJECTED",
                    name="withdrawal_status",
                ),
                server_default="REQUESTED",
                nullable=False,
            ),
            sa.Column("processed_by_admin_id", sa.UUID(), nullable=True),
            sa.Column("rejection_reason", sa.String(length=255), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
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
            sa.CheckConstraint("amount >= 50.00", name="chk_withdrawal_min"),
            sa.ForeignKeyConstraint(
                ["courier_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["processed_by_admin_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["wallet_id"],
                ["wallets.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("withdrawals")

    if "invoice_items" not in existing_tables:
        op.create_table(
            "invoice_items",
            sa.Column("invoice_id", sa.UUID(), nullable=False),
            sa.Column("position", sa.SmallInteger(), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column("unit_price_amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("tax_rate", sa.Numeric(precision=6, scale=4), nullable=False),
            sa.Column("line_net_amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column(
                "line_discount_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0.00"),
                nullable=False,
            ),
            sa.Column("line_taxable_amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("line_tax_amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("line_total_amount", sa.Numeric(precision=12, scale=2), nullable=False),
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
                "line_net_amount = unit_price_amount * quantity AND line_taxable_amount = line_net_amount - line_discount_amount AND line_total_amount = line_taxable_amount + line_tax_amount",
                name="chk_item_line_math",
            ),
            sa.CheckConstraint("quantity BETWEEN 1 AND 999", name="chk_item_quantity"),
            sa.CheckConstraint("tax_rate >= 0 AND tax_rate <= 1", name="chk_item_tax_rate"),
            sa.CheckConstraint("unit_price_amount > 0", name="chk_item_unit_price"),
            sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("invoice_id", "position", name="uq_invoice_item_position"),
        )
        existing_tables.add("invoice_items")

    if "messages" not in existing_tables:
        op.create_table(
            "messages",
            sa.Column("conversation_id", sa.UUID(), nullable=False),
            sa.Column("sender_id", sa.UUID(), nullable=False),
            sa.Column(
                "message_type",
                postgresql.ENUM("TEXT", "IMAGE", "VIDEO", "SYSTEM", "MIXED", name="message_type"),
                server_default="TEXT",
                nullable=False,
            ),
            sa.Column("content_encrypted", sa.Text(), nullable=False),
            sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["sender_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("messages")

    if "payment_intents" not in existing_tables:
        op.create_table(
            "payment_intents",
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column(
                "purpose",
                postgresql.ENUM("ORDER_INVOICE", "WALLET_TOPUP", name="payment_purpose"),
                nullable=False,
            ),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column(
                "wallet_reserved_amount",
                sa.Numeric(precision=12, scale=2),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "currency", sa.String(length=3), server_default=sa.text("'SAR'"), nullable=False
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "NEW", "PAID", "FAILED", "EXPIRED", "CANCELLED", name="payment_intent_status"
                ),
                server_default="NEW",
                nullable=False,
            ),
            sa.Column(
                "checkout_provider",
                sa.String(length=20),
                server_default=sa.text("'SIMULATED'"),
                nullable=False,
            ),
            sa.Column("gateway_reference", sa.String(length=100), nullable=True),
            sa.Column("gateway_payment_url", sa.String(length=512), nullable=True),
            sa.Column("gateway_customer_identifier", sa.String(length=100), nullable=True),
            sa.Column("reference_invoice_id", sa.UUID(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failure_reason", sa.String(length=255), nullable=True),
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
                "(purpose='ORDER_INVOICE' AND reference_invoice_id IS NOT NULL) OR (purpose='WALLET_TOPUP' AND reference_invoice_id IS NULL)",
                name="chk_intent_purpose_reference",
            ),
            sa.CheckConstraint(
                "purpose='ORDER_INVOICE' OR wallet_reserved_amount=0",
                name="chk_intent_reservation_purpose",
            ),
            sa.CheckConstraint("amount > 0", name="chk_intent_amount_positive"),
            sa.CheckConstraint(
                "wallet_reserved_amount >= 0", name="chk_intent_reservation_nonnegative"
            ),
            sa.ForeignKeyConstraint(
                ["reference_invoice_id"],
                ["invoices.id"],
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("payment_intents")

    if "payout_transfers" not in existing_tables:
        op.create_table(
            "payout_transfers",
            sa.Column("withdrawal_id", sa.UUID(), nullable=False),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("payment_reference", sa.String(length=100), nullable=False),
            sa.Column("supplier_id", sa.UUID(), nullable=False),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("uti", sa.String(length=100), nullable=True),
            sa.Column("failure_reason", sa.String(length=255), nullable=True),
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
            sa.ForeignKeyConstraint(["withdrawal_id"], ["withdrawals.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "provider", "payment_reference", name="uq_payout_transfers_provider_reference"
            ),
            sa.UniqueConstraint("withdrawal_id", name="uq_payout_transfers_withdrawal"),
        )
        existing_tables.add("payout_transfers")

    if "promo_redemptions" not in existing_tables:
        op.create_table(
            "promo_redemptions",
            sa.Column("promo_id", sa.UUID(), nullable=False),
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("invoice_id", sa.UUID(), nullable=False),
            sa.Column("order_id", sa.UUID(), nullable=False),
            sa.Column("discount_amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM("RESERVED", "CONSUMED", "RELEASED", name="promo_redemption_status"),
                server_default="RESERVED",
                nullable=False,
            ),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.CheckConstraint("discount_amount > 0", name="chk_redemption_amount_positive"),
            sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["order_id"],
                ["orders.id"],
            ),
            sa.ForeignKeyConstraint(["promo_id"], ["promos.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("promo_id", "invoice_id", name="uq_promo_redemption_invoice"),
        )
        existing_tables.add("promo_redemptions")

    if "message_attachments" not in existing_tables:
        op.create_table(
            "message_attachments",
            sa.Column("message_id", sa.UUID(), nullable=False),
            sa.Column("storage_key", sa.String(length=512), nullable=False),
            sa.Column("content_type", sa.String(length=50), nullable=False),
            sa.Column("byte_size", sa.BigInteger(), nullable=False),
            sa.Column(
                "display_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
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
            sa.CheckConstraint(
                "content_type IN ('image/jpeg', 'image/png')",
                name="chk_message_attachments_content_type",
            ),
            sa.CheckConstraint(
                "byte_size > 0 AND byte_size <= 10485760", name="chk_message_attachments_byte_size"
            ),
            sa.CheckConstraint(
                "display_order BETWEEN 0 AND 4", name="chk_message_attachments_display_order"
            ),
            sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "message_id", "storage_key", name="uq_message_attachments_message_key"
            ),
        )
        existing_tables.add("message_attachments")

    if "transactions" not in existing_tables:
        op.create_table(
            "transactions",
            sa.Column("wallet_id", sa.UUID(), nullable=False),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column(
                "type",
                postgresql.ENUM(
                    "TOPUP",
                    "WITHDRAWAL",
                    "ESCROW_HOLD",
                    "ESCROW_RELEASE",
                    "PAYMENT",
                    "REFUND",
                    "COMMISSION",
                    "SERVICE_FEE",
                    "TAX",
                    "PROMO_SUBSIDY",
                    name="transaction_type",
                ),
                nullable=False,
            ),
            sa.Column(
                "status",
                postgresql.ENUM("PENDING", "SETTLED", "REVERSED", name="transaction_status"),
                server_default="SETTLED",
                nullable=False,
            ),
            sa.Column("reference_order_id", sa.UUID(), nullable=True),
            sa.Column("reference_invoice_id", sa.UUID(), nullable=True),
            sa.Column("reference_intent_id", sa.UUID(), nullable=True),
            sa.Column("correlation_id", sa.UUID(), nullable=False),
            sa.Column("balance_after", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("description", sa.String(length=255), nullable=True),
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
            sa.CheckConstraint("amount <> 0", name="chk_amount_non_zero"),
            sa.ForeignKeyConstraint(
                ["reference_intent_id"], ["payment_intents.id"], ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(["reference_invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["reference_order_id"], ["orders.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
        )
        existing_tables.add("transactions")

    if "wallet_topups" not in existing_tables:
        op.create_table(
            "wallet_topups",
            sa.Column("user_id", sa.UUID(), nullable=False),
            sa.Column("wallet_id", sa.UUID(), nullable=False),
            sa.Column("payment_intent_id", sa.UUID(), nullable=False),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
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
            sa.CheckConstraint("amount >= 100.00 AND amount <= 20000.00", name="chk_topup_bounds"),
            sa.ForeignKeyConstraint(
                ["payment_intent_id"],
                ["payment_intents.id"],
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["wallet_id"],
                ["wallets.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("payment_intent_id", name="uq_wallet_topups_intent"),
        )
        existing_tables.add("wallet_topups")

    if "idx_dhamen_receipts_batch" not in existing_indexes.get(
        "dhamen_notification_receipts", set()
    ):
        op.create_index(
            "idx_dhamen_receipts_batch", "dhamen_notification_receipts", ["batch_id"], unique=False
        )
        existing_indexes.setdefault("dhamen_notification_receipts", set()).add(
            "idx_dhamen_receipts_batch"
        )

    if "idx_dhamen_receipts_payment_reference" not in existing_indexes.get(
        "dhamen_notification_receipts", set()
    ):
        op.create_index(
            "idx_dhamen_receipts_payment_reference",
            "dhamen_notification_receipts",
            ["payment_reference"],
            unique=False,
            postgresql_where=sa.text("payment_reference IS NOT NULL"),
        )
        existing_indexes.setdefault("dhamen_notification_receipts", set()).add(
            "idx_dhamen_receipts_payment_reference"
        )

    if "idx_dhamen_receipts_pending" not in existing_indexes.get(
        "dhamen_notification_receipts", set()
    ):
        op.create_index(
            "idx_dhamen_receipts_pending",
            "dhamen_notification_receipts",
            ["created_at"],
            unique=False,
            postgresql_where=sa.text("processed_at IS NULL"),
        )
        existing_indexes.setdefault("dhamen_notification_receipts", set()).add(
            "idx_dhamen_receipts_pending"
        )

    if "idx_featured_gifts_active_order" not in existing_indexes.get("featured_gifts", set()):
        op.create_index(
            "idx_featured_gifts_active_order",
            "featured_gifts",
            ["is_active", "display_order"],
            unique=False,
        )
        existing_indexes.setdefault("featured_gifts", set()).add("idx_featured_gifts_active_order")

    if "idx_featured_gifts_category" not in existing_indexes.get("featured_gifts", set()):
        op.create_index("idx_featured_gifts_category", "featured_gifts", ["category"], unique=False)
        existing_indexes.setdefault("featured_gifts", set()).add("idx_featured_gifts_category")

    if "idx_users_role_status" not in existing_indexes.get("users", set()):
        op.create_index("idx_users_role_status", "users", ["role", "status"], unique=False)
        existing_indexes.setdefault("users", set()).add("idx_users_role_status")

    if "uq_users_email" not in existing_indexes.get("users", set()):
        op.create_index(
            "uq_users_email",
            "users",
            ["email"],
            unique=True,
            postgresql_where=sa.text("email IS NOT NULL"),
        )
        existing_indexes.setdefault("users", set()).add("uq_users_email")

    if "uq_users_gateway_customer_identifier" not in existing_indexes.get("users", set()):
        op.create_index(
            "uq_users_gateway_customer_identifier",
            "users",
            ["gateway_customer_identifier"],
            unique=True,
            postgresql_where=sa.text("gateway_customer_identifier IS NOT NULL"),
        )
        existing_indexes.setdefault("users", set()).add("uq_users_gateway_customer_identifier")

    if "idx_admin_sessions_admin_expires" not in existing_indexes.get("admin_sessions", set()):
        op.create_index(
            "idx_admin_sessions_admin_expires",
            "admin_sessions",
            ["admin_user_id", "expires_at"],
            unique=False,
        )
        existing_indexes.setdefault("admin_sessions", set()).add("idx_admin_sessions_admin_expires")

    if "idx_audit_logs_actor_created" not in existing_indexes.get("audit_logs", set()):
        op.create_index(
            "idx_audit_logs_actor_created",
            "audit_logs",
            ["actor_user_id", sa.literal_column("created_at DESC")],
            unique=False,
        )
        existing_indexes.setdefault("audit_logs", set()).add("idx_audit_logs_actor_created")

    if "idx_audit_logs_entity" not in existing_indexes.get("audit_logs", set()):
        op.create_index(
            "idx_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"], unique=False
        )
        existing_indexes.setdefault("audit_logs", set()).add("idx_audit_logs_entity")

    if "idx_courier_portfolios_courier" not in existing_indexes.get("courier_portfolios", set()):
        op.create_index(
            "idx_courier_portfolios_courier",
            "courier_portfolios",
            ["courier_id", "display_order"],
            unique=False,
        )
        existing_indexes.setdefault("courier_portfolios", set()).add(
            "idx_courier_portfolios_courier"
        )

    if "idx_courier_profiles_city_verified" not in existing_indexes.get("courier_profiles", set()):
        op.create_index(
            "idx_courier_profiles_city_verified",
            "courier_profiles",
            ["city_of_residence_id", "is_verified"],
            unique=False,
        )
        existing_indexes.setdefault("courier_profiles", set()).add(
            "idx_courier_profiles_city_verified"
        )

    if "uq_courier_identity_fingerprint" not in existing_indexes.get("courier_profiles", set()):
        op.create_index(
            "uq_courier_identity_fingerprint",
            "courier_profiles",
            ["identity_fingerprint"],
            unique=True,
            postgresql_where=sa.text("identity_fingerprint IS NOT NULL"),
        )
        existing_indexes.setdefault("courier_profiles", set()).add(
            "uq_courier_identity_fingerprint"
        )

    if "uq_courier_profiles_gateway_supplier" not in existing_indexes.get(
        "courier_profiles", set()
    ):
        op.create_index(
            "uq_courier_profiles_gateway_supplier",
            "courier_profiles",
            ["gateway_supplier_id"],
            unique=True,
            postgresql_where=sa.text("gateway_supplier_id IS NOT NULL"),
        )
        existing_indexes.setdefault("courier_profiles", set()).add(
            "uq_courier_profiles_gateway_supplier"
        )

    if "idx_device_tokens_user" not in existing_indexes.get("device_tokens", set()):
        op.create_index("idx_device_tokens_user", "device_tokens", ["user_id"], unique=False)
        existing_indexes.setdefault("device_tokens", set()).add("idx_device_tokens_user")

    if "idx_occasions_user_date" not in existing_indexes.get("occasions", set()):
        op.create_index(
            "idx_occasions_user_date", "occasions", ["user_id", "occasion_date"], unique=False
        )
        existing_indexes.setdefault("occasions", set()).add("idx_occasions_user_date")

    if "idx_orders_city_status" not in existing_indexes.get("orders", set()):
        op.create_index(
            "idx_orders_city_status", "orders", ["delivery_city_id", "status"], unique=False
        )
        existing_indexes.setdefault("orders", set()).add("idx_orders_city_status")

    if "idx_orders_courier_created" not in existing_indexes.get("orders", set()):
        op.create_index(
            "idx_orders_courier_created",
            "orders",
            ["courier_id", sa.literal_column("created_at DESC")],
            unique=False,
            postgresql_where=sa.text("courier_id IS NOT NULL"),
        )
        existing_indexes.setdefault("orders", set()).add("idx_orders_courier_created")

    if "idx_orders_customer_created" not in existing_indexes.get("orders", set()):
        op.create_index(
            "idx_orders_customer_created",
            "orders",
            ["customer_id", sa.literal_column("created_at DESC")],
            unique=False,
        )
        existing_indexes.setdefault("orders", set()).add("idx_orders_customer_created")

    if "idx_orders_status_delivered_at" not in existing_indexes.get("orders", set()):
        op.create_index(
            "idx_orders_status_delivered_at",
            "orders",
            ["status", "delivered_at"],
            unique=False,
            postgresql_where=sa.text("status = 'DELIVERED'"),
        )
        existing_indexes.setdefault("orders", set()).add("idx_orders_status_delivered_at")

    if "idx_promos_active_window" not in existing_indexes.get("promos", set()):
        op.create_index(
            "idx_promos_active_window",
            "promos",
            ["is_active", "starts_at", "ends_at"],
            unique=False,
        )
        existing_indexes.setdefault("promos", set()).add("idx_promos_active_window")

    if "idx_refresh_tokens_expiry" not in existing_indexes.get("refresh_tokens", set()):
        op.create_index(
            "idx_refresh_tokens_expiry", "refresh_tokens", ["expires_at", "id"], unique=False
        )
        existing_indexes.setdefault("refresh_tokens", set()).add("idx_refresh_tokens_expiry")

    if "idx_refresh_tokens_family" not in existing_indexes.get("refresh_tokens", set()):
        op.create_index("idx_refresh_tokens_family", "refresh_tokens", ["family_id"], unique=False)
        existing_indexes.setdefault("refresh_tokens", set()).add("idx_refresh_tokens_family")

    if "idx_refresh_tokens_user" not in existing_indexes.get("refresh_tokens", set()):
        op.create_index("idx_refresh_tokens_user", "refresh_tokens", ["user_id"], unique=False)
        existing_indexes.setdefault("refresh_tokens", set()).add("idx_refresh_tokens_user")

    if "uq_wallets_one_escrow" not in existing_indexes.get("wallets", set()):
        op.create_index(
            "uq_wallets_one_escrow",
            "wallets",
            ["type"],
            unique=True,
            postgresql_where=sa.text("type='SYSTEM_ESCROW'"),
        )
        existing_indexes.setdefault("wallets", set()).add("uq_wallets_one_escrow")

    if "uq_wallets_one_gateway" not in existing_indexes.get("wallets", set()):
        op.create_index(
            "uq_wallets_one_gateway",
            "wallets",
            ["type"],
            unique=True,
            postgresql_where=sa.text("type='SYSTEM_GATEWAY'"),
        )
        existing_indexes.setdefault("wallets", set()).add("uq_wallets_one_gateway")

    if "uq_wallets_one_revenue" not in existing_indexes.get("wallets", set()):
        op.create_index(
            "uq_wallets_one_revenue",
            "wallets",
            ["type"],
            unique=True,
            postgresql_where=sa.text("type='SYSTEM_REVENUE'"),
        )
        existing_indexes.setdefault("wallets", set()).add("uq_wallets_one_revenue")

    if "uq_wallets_one_tax" not in existing_indexes.get("wallets", set()):
        op.create_index(
            "uq_wallets_one_tax",
            "wallets",
            ["type"],
            unique=True,
            postgresql_where=sa.text("type='SYSTEM_TAX_PAYABLE'"),
        )
        existing_indexes.setdefault("wallets", set()).add("uq_wallets_one_tax")

    if "uq_wallets_user" not in existing_indexes.get("wallets", set()):
        op.create_index(
            "uq_wallets_user",
            "wallets",
            ["user_id"],
            unique=True,
            postgresql_where=sa.text("user_id IS NOT NULL"),
        )
        existing_indexes.setdefault("wallets", set()).add("uq_wallets_user")

    if "idx_conversations_courier_inbox" not in existing_indexes.get("conversations", set()):
        op.create_index(
            "idx_conversations_courier_inbox",
            "conversations",
            ["courier_id", sa.literal_column("last_message_timestamp DESC")],
            unique=False,
        )
        existing_indexes.setdefault("conversations", set()).add("idx_conversations_courier_inbox")

    if "idx_conversations_customer_inbox" not in existing_indexes.get("conversations", set()):
        op.create_index(
            "idx_conversations_customer_inbox",
            "conversations",
            ["customer_id", sa.literal_column("last_message_timestamp DESC")],
            unique=False,
        )
        existing_indexes.setdefault("conversations", set()).add("idx_conversations_customer_inbox")

    if "idx_disputes_status_created" not in existing_indexes.get("disputes", set()):
        op.create_index(
            "idx_disputes_status_created", "disputes", ["status", "created_at"], unique=False
        )
        existing_indexes.setdefault("disputes", set()).add("idx_disputes_status_created")

    if "idx_invoices_order" not in existing_indexes.get("invoices", set()):
        op.create_index("idx_invoices_order", "invoices", ["order_id"], unique=False)
        existing_indexes.setdefault("invoices", set()).add("idx_invoices_order")

    if "idx_invoices_promo" not in existing_indexes.get("invoices", set()):
        op.create_index(
            "idx_invoices_promo",
            "invoices",
            ["promo_id"],
            unique=False,
            postgresql_where=sa.text("promo_id IS NOT NULL"),
        )
        existing_indexes.setdefault("invoices", set()).add("idx_invoices_promo")

    if "idx_invoices_receipt_pending" not in existing_indexes.get("invoices", set()):
        op.create_index(
            "idx_invoices_receipt_pending",
            "invoices",
            ["id"],
            unique=False,
            postgresql_where=sa.text("status='PAID' AND receipt_email_sent_at IS NULL"),
        )
        existing_indexes.setdefault("invoices", set()).add("idx_invoices_receipt_pending")

    if "idx_invoices_status_expires" not in existing_indexes.get("invoices", set()):
        op.create_index(
            "idx_invoices_status_expires",
            "invoices",
            ["status", "expires_at"],
            unique=False,
            postgresql_where=sa.text("status='ISSUED'"),
        )
        existing_indexes.setdefault("invoices", set()).add("idx_invoices_status_expires")

    if "uq_invoices_one_active_per_order" not in existing_indexes.get("invoices", set()):
        op.create_index(
            "uq_invoices_one_active_per_order",
            "invoices",
            ["order_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('DRAFT','ISSUED','PAID')"),
        )
        existing_indexes.setdefault("invoices", set()).add("uq_invoices_one_active_per_order")

    if "idx_order_media_order_type" not in existing_indexes.get("order_media", set()):
        op.create_index(
            "idx_order_media_order_type", "order_media", ["order_id", "media_type"], unique=False
        )
        existing_indexes.setdefault("order_media", set()).add("idx_order_media_order_type")

    if "idx_ratings_rated_user" not in existing_indexes.get("ratings", set()):
        op.create_index("idx_ratings_rated_user", "ratings", ["rated_user_id"], unique=False)
        existing_indexes.setdefault("ratings", set()).add("idx_ratings_rated_user")

    if "idx_withdrawals_courier_status" not in existing_indexes.get("withdrawals", set()):
        op.create_index(
            "idx_withdrawals_courier_status", "withdrawals", ["courier_id", "status"], unique=False
        )
        existing_indexes.setdefault("withdrawals", set()).add("idx_withdrawals_courier_status")

    if "idx_withdrawals_status_created" not in existing_indexes.get("withdrawals", set()):
        op.create_index(
            "idx_withdrawals_status_created", "withdrawals", ["status", "created_at"], unique=False
        )
        existing_indexes.setdefault("withdrawals", set()).add("idx_withdrawals_status_created")

    if "uq_withdrawals_courier_idempotency" not in existing_indexes.get("withdrawals", set()):
        op.create_index(
            "uq_withdrawals_courier_idempotency",
            "withdrawals",
            ["courier_id", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )
        existing_indexes.setdefault("withdrawals", set()).add("uq_withdrawals_courier_idempotency")

    if "idx_invoice_items_invoice" not in existing_indexes.get("invoice_items", set()):
        op.create_index(
            "idx_invoice_items_invoice", "invoice_items", ["invoice_id", "position"], unique=False
        )
        existing_indexes.setdefault("invoice_items", set()).add("idx_invoice_items_invoice")

    if "idx_messages_conversation_keyset" not in existing_indexes.get("messages", set()):
        op.create_index(
            "idx_messages_conversation_keyset",
            "messages",
            ["conversation_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
            unique=False,
        )
        existing_indexes.setdefault("messages", set()).add("idx_messages_conversation_keyset")

    if "idx_payment_intents_invoice" not in existing_indexes.get("payment_intents", set()):
        op.create_index(
            "idx_payment_intents_invoice",
            "payment_intents",
            ["reference_invoice_id"],
            unique=False,
            postgresql_where=sa.text("reference_invoice_id IS NOT NULL"),
        )
        existing_indexes.setdefault("payment_intents", set()).add("idx_payment_intents_invoice")

    if "idx_payment_intents_status_expires" not in existing_indexes.get("payment_intents", set()):
        op.create_index(
            "idx_payment_intents_status_expires",
            "payment_intents",
            ["status", "expires_at"],
            unique=False,
            postgresql_where=sa.text("status='NEW'"),
        )
        existing_indexes.setdefault("payment_intents", set()).add(
            "idx_payment_intents_status_expires"
        )

    if "idx_payment_intents_user_created" not in existing_indexes.get("payment_intents", set()):
        op.create_index(
            "idx_payment_intents_user_created",
            "payment_intents",
            ["user_id", sa.literal_column("created_at DESC")],
            unique=False,
        )
        existing_indexes.setdefault("payment_intents", set()).add(
            "idx_payment_intents_user_created"
        )

    if "uq_payment_intents_gateway_reference" not in existing_indexes.get("payment_intents", set()):
        op.create_index(
            "uq_payment_intents_gateway_reference",
            "payment_intents",
            ["checkout_provider", "gateway_reference"],
            unique=True,
            postgresql_where=sa.text("gateway_reference IS NOT NULL"),
        )
        existing_indexes.setdefault("payment_intents", set()).add(
            "uq_payment_intents_gateway_reference"
        )

    if "idx_payout_transfers_status_submitted" not in existing_indexes.get(
        "payout_transfers", set()
    ):
        op.create_index(
            "idx_payout_transfers_status_submitted",
            "payout_transfers",
            ["status", "submitted_at"],
            unique=False,
        )
        existing_indexes.setdefault("payout_transfers", set()).add(
            "idx_payout_transfers_status_submitted"
        )

    if "idx_promo_redemptions_invoice" not in existing_indexes.get("promo_redemptions", set()):
        op.create_index(
            "idx_promo_redemptions_invoice", "promo_redemptions", ["invoice_id"], unique=False
        )
        existing_indexes.setdefault("promo_redemptions", set()).add("idx_promo_redemptions_invoice")

    if "idx_promo_redemptions_promo_user" not in existing_indexes.get("promo_redemptions", set()):
        op.create_index(
            "idx_promo_redemptions_promo_user",
            "promo_redemptions",
            ["promo_id", "user_id", "status"],
            unique=False,
        )
        existing_indexes.setdefault("promo_redemptions", set()).add(
            "idx_promo_redemptions_promo_user"
        )

    if "idx_message_attachments_message_order" not in existing_indexes.get(
        "message_attachments", set()
    ):
        op.create_index(
            "idx_message_attachments_message_order",
            "message_attachments",
            ["message_id", "display_order"],
            unique=False,
        )
        existing_indexes.setdefault("message_attachments", set()).add(
            "idx_message_attachments_message_order"
        )

    if "idx_transactions_correlation" not in existing_indexes.get("transactions", set()):
        op.create_index(
            "idx_transactions_correlation", "transactions", ["correlation_id"], unique=False
        )
        existing_indexes.setdefault("transactions", set()).add("idx_transactions_correlation")

    if "idx_transactions_order" not in existing_indexes.get("transactions", set()):
        op.create_index(
            "idx_transactions_order",
            "transactions",
            ["reference_order_id"],
            unique=False,
            postgresql_where=sa.text("reference_order_id IS NOT NULL"),
        )
        existing_indexes.setdefault("transactions", set()).add("idx_transactions_order")

    if "idx_transactions_wallet_created" not in existing_indexes.get("transactions", set()):
        op.create_index(
            "idx_transactions_wallet_created",
            "transactions",
            ["wallet_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
            unique=False,
        )
        existing_indexes.setdefault("transactions", set()).add("idx_transactions_wallet_created")

    if "uq_transactions_idempotency" not in existing_indexes.get("transactions", set()):
        op.create_index(
            "uq_transactions_idempotency",
            "transactions",
            ["idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )
        existing_indexes.setdefault("transactions", set()).add("uq_transactions_idempotency")

    if "idx_wallet_topups_user_created" not in existing_indexes.get("wallet_topups", set()):
        op.create_index(
            "idx_wallet_topups_user_created",
            "wallet_topups",
            ["user_id", sa.literal_column("created_at DESC")],
            unique=False,
        )
        existing_indexes.setdefault("wallet_topups", set()).add("idx_wallet_topups_user_created")

    seed_cities = offline
    if not offline:
        assert bind is not None
        seed_cities = not bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM cities)")).scalar_one()
    if seed_cities:
        op.bulk_insert(
            sa.table(
                "cities",
                sa.column("name", sa.String()),
                sa.column("shortcut", sa.String()),
                sa.column("is_active", sa.Boolean()),
            ),
            [
                {"name": "Riyadh", "shortcut": "RUH", "is_active": True},
                {"name": "Jeddah", "shortcut": "JED", "is_active": True},
                {"name": "Makkah", "shortcut": "MKK", "is_active": True},
                {"name": "Madinah", "shortcut": "MED", "is_active": True},
                {"name": "Dammam", "shortcut": "DMM", "is_active": True},
                {"name": "Al Khobar", "shortcut": "KBR", "is_active": True},
                {"name": "Tabuk", "shortcut": "TUU", "is_active": True},
                {"name": "Hail", "shortcut": "HAS", "is_active": True},
                {"name": "Buraydah", "shortcut": "BUR", "is_active": True},
                {"name": "Sakaka", "shortcut": "SKK", "is_active": True},
                {"name": "Arar", "shortcut": "RAE", "is_active": True},
                {"name": "Taif", "shortcut": "TIF", "is_active": True},
                {"name": "Al Baha", "shortcut": "BAH", "is_active": True},
                {"name": "Abha", "shortcut": "AHB", "is_active": True},
                {"name": "Khamis Mushayt", "shortcut": "KMX", "is_active": True},
                {"name": "Najran", "shortcut": "EAM", "is_active": True},
                {"name": "Jazan", "shortcut": "GIZ", "is_active": True},
                {"name": "Al Hofuf", "shortcut": "HOF", "is_active": True},
                {"name": "Yanbu", "shortcut": "YNB", "is_active": True},
                {"name": "Jubail", "shortcut": "JUB", "is_active": True},
            ],
        )

    wallet_seed_statements = (
        (
            "SYSTEM_ESCROW",
            "INSERT INTO wallets (type, user_id) SELECT 'SYSTEM_ESCROW'::wallet_type, NULL "
            "WHERE NOT EXISTS (SELECT 1 FROM wallets WHERE type = 'SYSTEM_ESCROW'::wallet_type)",
        ),
        (
            "SYSTEM_REVENUE",
            "INSERT INTO wallets (type, user_id) SELECT 'SYSTEM_REVENUE'::wallet_type, NULL "
            "WHERE NOT EXISTS (SELECT 1 FROM wallets WHERE type = 'SYSTEM_REVENUE'::wallet_type)",
        ),
        (
            "SYSTEM_GATEWAY",
            "INSERT INTO wallets (type, user_id) SELECT 'SYSTEM_GATEWAY'::wallet_type, NULL "
            "WHERE NOT EXISTS (SELECT 1 FROM wallets WHERE type = 'SYSTEM_GATEWAY'::wallet_type)",
        ),
        (
            "SYSTEM_TAX_PAYABLE",
            "INSERT INTO wallets (type, user_id) SELECT 'SYSTEM_TAX_PAYABLE'::wallet_type, NULL "
            "WHERE NOT EXISTS (SELECT 1 FROM wallets WHERE type = 'SYSTEM_TAX_PAYABLE'::wallet_type)",
        ),
    )
    for wallet_type, statement in wallet_seed_statements:
        if offline:
            op.execute(sa.text(statement))
        else:
            assert bind is not None
            bind.execute(
                sa.text(
                    "INSERT INTO wallets (type, user_id) "
                    "SELECT CAST(:wallet_type AS wallet_type), NULL "
                    "WHERE NOT EXISTS ("
                    "SELECT 1 FROM wallets WHERE type = CAST(:wallet_type AS wallet_type))"
                ),
                {"wallet_type": wallet_type},
            )


def downgrade() -> None:
    """Drop the initial Giftly schema."""
    op.drop_table("wallet_topups")
    op.drop_table("transactions")
    op.drop_table("message_attachments")
    op.drop_table("promo_redemptions")
    op.drop_table("payout_transfers")
    op.drop_table("payment_intents")
    op.drop_table("messages")
    op.drop_table("invoice_items")
    op.drop_table("withdrawals")
    op.drop_table("ratings")
    op.drop_table("order_media")
    op.drop_table("invoices")
    op.drop_table("disputes")
    op.drop_table("conversations")
    op.drop_table("wallets")
    op.drop_table("refresh_tokens")
    op.drop_table("promos")
    op.drop_table("orders")
    op.drop_table("occasions")
    op.drop_table("media_uploads")
    op.drop_table("device_tokens")
    op.drop_table("courier_profiles")
    op.drop_table("courier_portfolios")
    op.drop_table("audit_logs")
    op.drop_table("admin_sessions")
    op.drop_table("users")
    op.drop_table("otp_attempts")
    op.drop_table("featured_gifts")
    op.drop_table("dhamen_notification_receipts")
    op.drop_table("cities")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS gateway_customer_identifier_seq"))
    op.execute(sa.text("DROP TYPE IF EXISTS withdrawal_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS wallet_type"))
    op.execute(sa.text("DROP TYPE IF EXISTS user_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS user_role"))
    op.execute(sa.text("DROP TYPE IF EXISTS transaction_type"))
    op.execute(sa.text("DROP TYPE IF EXISTS transaction_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS promo_redemption_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS promo_discount_type"))
    op.execute(sa.text("DROP TYPE IF EXISTS payment_purpose"))
    op.execute(sa.text("DROP TYPE IF EXISTS payment_method"))
    op.execute(sa.text("DROP TYPE IF EXISTS payment_intent_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS order_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS message_type"))
    op.execute(sa.text("DROP TYPE IF EXISTS media_type"))
    op.execute(sa.text("DROP TYPE IF EXISTS invoice_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS dispute_status"))
    op.execute(sa.text("DROP TYPE IF EXISTS device_os"))
