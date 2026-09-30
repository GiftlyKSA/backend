"""Record committed data changes and remove historical HTTP request audit rows."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_action_only_audit"
down_revision = "0008_audit_trail_indexes"
branch_labels = None
depends_on = None

_TABLE_KEYS = {
    "cities": "id",
    "users": "id",
    "courier_profiles": "user_id",
    "courier_portfolios": "id",
    "media_uploads": "id",
    "featured_gifts": "id",
    "occasions": "id",
    "device_tokens": "id",
    "orders": "id",
    "order_notifications": "order_id",
    "order_media": "id",
    "promos": "id",
    "invoices": "id",
    "invoice_items": "id",
    "promo_redemptions": "id",
    "payment_intents": "id",
    "dhamen_notification_receipts": "id",
    "wallet_topups": "id",
    "conversations": "id",
    "messages": "id",
    "message_attachments": "id",
    "wallets": "id",
    "transactions": "id",
    "withdrawals": "id",
    "payout_transfers": "id",
    "ratings": "id",
    "disputes": "id",
}


def upgrade() -> None:
    """Install metadata-only change auditing and purge request audit noise."""
    op.execute(
        sa.text(
            """
            CREATE FUNCTION giftly_audit_row_change() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE
              origin text := NULLIF(current_setting('giftly.audit_origin', true), '');
              category text := NULLIF(current_setting('giftly.audit_category', true), '');
              actor_text text := NULLIF(current_setting('giftly.audit_actor_id', true), '');
              actor_id uuid;
              record_id uuid;
            BEGIN
              IF TG_OP = 'UPDATE' THEN
                IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
                IF TG_TABLE_NAME = 'users'
                   AND to_jsonb(NEW) - 'auth_version' - 'updated_at'
                       = to_jsonb(OLD) - 'auth_version' - 'updated_at' THEN
                  RETURN NEW;
                END IF;
              END IF;

              IF origin = 'request' AND category IS NULL THEN
                RAISE EXCEPTION 'Audit actor is required for a request data change';
              END IF;
              category := COALESCE(category, 'SYSTEM');
              IF category NOT IN ('CUSTOMER', 'COURIER', 'ADMIN', 'SYSTEM') THEN
                RAISE EXCEPTION 'Invalid audit actor category';
              END IF;
              IF actor_text IS NOT NULL THEN actor_id := actor_text::uuid; END IF;

              IF TG_OP = 'DELETE' THEN
                record_id := (to_jsonb(OLD) ->> TG_ARGV[0])::uuid;
              ELSE
                record_id := (to_jsonb(NEW) ->> TG_ARGV[0])::uuid;
              END IF;
              IF TG_TABLE_NAME = 'users' AND TG_OP = 'INSERT'
                 AND category IN ('CUSTOMER', 'COURIER') AND actor_id IS NULL THEN
                actor_id := record_id;
              END IF;
              IF TG_TABLE_NAME = 'wallets' AND TG_OP = 'INSERT'
                 AND category IN ('CUSTOMER', 'COURIER') AND actor_id IS NULL THEN
                actor_id := (to_jsonb(NEW) ->> 'user_id')::uuid;
              END IF;
              IF category <> 'SYSTEM' AND actor_id IS NULL THEN
                RAISE EXCEPTION 'Audit user is required for a human data change';
              END IF;
              IF category = 'SYSTEM' THEN actor_id := NULL; END IF;

              INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, metadata)
              VALUES (
                actor_id,
                CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'UPDATE' THEN 'UPDATE' ELSE 'DELETE' END,
                TG_TABLE_NAME,
                record_id,
                jsonb_build_object('actor_category', category)
              );
              IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
              RETURN NEW;
            END
            $$
            """
        )
    )
    for table, key in _TABLE_KEYS.items():
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_giftly_audit_change AFTER INSERT OR UPDATE OR DELETE "
                f"ON {table} FOR EACH ROW EXECUTE FUNCTION giftly_audit_row_change('{key}')"
            )
        )
    op.execute(sa.text("DELETE FROM audit_logs WHERE left(action, 5) = 'HTTP_'"))


def downgrade() -> None:
    """Remove change triggers; deleted historical HTTP rows require a database backup."""
    for table in reversed(tuple(_TABLE_KEYS)):
        op.execute(sa.text(f"DROP TRIGGER trg_giftly_audit_change ON {table}"))
    op.execute(sa.text("DROP FUNCTION giftly_audit_row_change()"))
