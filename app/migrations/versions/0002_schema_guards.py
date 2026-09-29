"""Protect ledger, issued invoice items, and messages in PostgreSQL."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_schema_guards"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install database triggers that enforce immutable record invariants."""
    statements = (
        """
        CREATE OR REPLACE FUNCTION giftly_admin_maintenance_allowed()
        RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT EXISTS (
            SELECT 1 FROM admin_sessions AS admin_session
            JOIN users AS actor ON actor.id = admin_session.admin_user_id
            WHERE admin_session.id = NULLIF(current_setting('giftly.admin_session', true), '')::uuid
              AND actor.id = NULLIF(current_setting('giftly.admin_actor', true), '')::uuid
              AND admin_session.revoked_at IS NULL AND admin_session.expires_at > now()
              AND actor.role = 'ADMIN' AND actor.status = 'ACTIVE'
              AND actor.deleted_at IS NULL
          )
        $$
        """,
        """
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
          RAISE EXCEPTION 'Ledger entries are immutable';
        END
        $$
        """,
        """
        CREATE OR REPLACE FUNCTION giftly_guard_invoice_item()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_invoice_id uuid;
        BEGIN
          IF giftly_admin_maintenance_allowed() THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'DELETE' THEN
            target_invoice_id := OLD.invoice_id;
          ELSE
            target_invoice_id := NEW.invoice_id;
          END IF;
          IF EXISTS (
            SELECT 1 FROM invoices WHERE id = target_invoice_id AND status = 'DRAFT'
          ) THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Invoice items are frozen after issue';
        END
        $$
        """,
        """
        CREATE OR REPLACE FUNCTION giftly_guard_message()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF giftly_admin_maintenance_allowed() THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END IF;
          IF TG_OP = 'UPDATE'
             AND to_jsonb(NEW) - 'is_read' - 'read_at' - 'updated_at'
                 = to_jsonb(OLD) - 'is_read' - 'read_at' - 'updated_at' THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'Messages are immutable';
        END
        $$
        """,
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_giftly_guard_transaction'
              AND tgrelid = 'transactions'::regclass
          ) THEN
            CREATE TRIGGER trg_giftly_guard_transaction
              BEFORE UPDATE OR DELETE ON transactions
              FOR EACH ROW EXECUTE FUNCTION giftly_guard_transaction();
          END IF;
        END $$
        """,
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_giftly_guard_invoice_item'
              AND tgrelid = 'invoice_items'::regclass
          ) THEN
            CREATE TRIGGER trg_giftly_guard_invoice_item
              BEFORE INSERT OR UPDATE OR DELETE ON invoice_items
              FOR EACH ROW EXECUTE FUNCTION giftly_guard_invoice_item();
          END IF;
        END $$
        """,
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_giftly_guard_message'
              AND tgrelid = 'messages'::regclass
          ) THEN
            CREATE TRIGGER trg_giftly_guard_message
              BEFORE UPDATE OR DELETE ON messages
              FOR EACH ROW EXECUTE FUNCTION giftly_guard_message();
          END IF;
        END $$
        """,
    )
    for statement in statements:
        op.execute(sa.text(statement))


def downgrade() -> None:
    """Remove the triggers and functions introduced by this revision."""
    for trigger_name, table_name in (
        ("trg_giftly_guard_message", "messages"),
        ("trg_giftly_guard_invoice_item", "invoice_items"),
        ("trg_giftly_guard_transaction", "transactions"),
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}"))
    for function_name in (
        "giftly_guard_message",
        "giftly_guard_invoice_item",
        "giftly_guard_transaction",
        "giftly_admin_maintenance_allowed",
    ):
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {function_name}()"))
