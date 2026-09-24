"""Permit scoped authenticated admin maintenance through immutable-row triggers.

Revision ID: d4e5f6a7b8c9
Revises: c9d0e1f2a3b4
"""

from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

_GUARD = """
  IF giftly_admin_maintenance_allowed() THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
  END IF;
"""

_FUNCTIONS = {
    "enforce_ledger_immutability": """
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'transactions are append-only: DELETE is forbidden';
  END IF;
  IF OLD.status <> 'PENDING' THEN
    RAISE EXCEPTION 'ledger row is immutable once it is not PENDING';
  END IF;
  IF NEW.status NOT IN ('SETTLED','REVERSED') THEN
    RAISE EXCEPTION 'ledger status may only move PENDING to SETTLED or REVERSED';
  END IF;
  IF NEW.amount <> OLD.amount OR NEW.wallet_id <> OLD.wallet_id
     OR NEW.type <> OLD.type OR NEW.correlation_id <> OLD.correlation_id THEN
    RAISE EXCEPTION 'amount, wallet_id, type, correlation_id are immutable';
  END IF;
  RETURN NEW;
END;
""",
    "enforce_invoice_item_freeze": """
DECLARE inv_status invoice_status;
BEGIN
  SELECT status INTO inv_status FROM invoices
    WHERE id = COALESCE(NEW.invoice_id, OLD.invoice_id);
  IF inv_status IS NULL THEN RETURN COALESCE(NEW, OLD); END IF;
  IF inv_status <> 'DRAFT' THEN
    RAISE EXCEPTION 'invoice items may only change while the invoice is DRAFT';
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
""",
    "enforce_message_append_only": """
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'messages are append-only: DELETE is forbidden';
  END IF;
  IF NEW.conversation_id <> OLD.conversation_id OR NEW.sender_id <> OLD.sender_id
     OR NEW.message_type <> OLD.message_type OR NEW.content_encrypted <> OLD.content_encrypted
     OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'messages are immutable except is_read/read_at';
  END IF;
  RETURN NEW;
END;
""",
}


def _install(*, allow_admin: bool) -> None:
    for name, original in _FUNCTIONS.items():
        body = original.replace("BEGIN", f"BEGIN\n{_GUARD}", 1) if allow_admin else original
        op.execute(
            f"CREATE OR REPLACE FUNCTION {name}() RETURNS trigger AS $$ {body} $$ LANGUAGE plpgsql"
        )


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION giftly_admin_maintenance_allowed() RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT EXISTS (
            SELECT 1 FROM public.admin_sessions AS sessions
            JOIN public.users AS admins ON admins.id = sessions.admin_user_id
            WHERE sessions.id::text = current_setting('giftly.admin_session', true)
              AND admins.id::text = current_setting('giftly.admin_actor', true)
              AND sessions.revoked_at IS NULL AND sessions.expires_at > now()
              AND sessions.created_at > now() - INTERVAL '12 hours'
              AND admins.role = 'ADMIN' AND admins.status = 'ACTIVE'
              AND admins.deleted_at IS NULL
          );
        $$;
    """)
    _install(allow_admin=True)


def downgrade() -> None:
    _install(allow_admin=False)
    op.execute("DROP FUNCTION giftly_admin_maintenance_allowed()")
