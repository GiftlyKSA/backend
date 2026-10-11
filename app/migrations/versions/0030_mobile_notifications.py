"""Capture neutral mobile events atomically and opt-in one-off reminders.

Historical status events are intentionally not backfilled. Existing occasions remain disabled.
Upgrade takes ordinary DDL locks on origin tables; use a maintenance window on busy databases.
Downgrade refuses populated mobile history/inbox or opted-in reminders; preserve them first.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_mobile_notifications"
down_revision = "0029_courier_report_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install capture triggers before accepting mobile writes."""
    op.execute(
        """
ALTER TABLE occasions ADD COLUMN reminder_enabled boolean NOT NULL DEFAULT false,
ADD COLUMN reminder_version integer NOT NULL DEFAULT 1,
ADD CONSTRAINT chk_occasions_reminder_version CHECK (reminder_version > 0)
        """
    )
    op.execute(
        """
CREATE TABLE inbox_notifications (
id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON
DELETE CASCADE,
event_key varchar(160) NOT NULL, notification_type varchar(40) NOT NULL,
destination_kind varchar(16) NOT NULL, resource_id uuid NOT NULL, status varchar(32),
read_at timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
CONSTRAINT uq_inbox_notification_event UNIQUE(user_id,event_key),
CONSTRAINT chk_inbox_event CHECK (
(notification_type='ORDER_STATUS_CHANGED' AND destination_kind='ORDER'
AND status IS NOT NULL AND status IN
('NEW','ASSIGNED','WAITING_PAYMENT','IN_PROGRESS','DELIVERED','COMPLETED','CANCELLED','DISPUTED','REFUNDED'))
OR (notification_type='INVOICE_ISSUED' AND destination_kind='ORDER' AND status IS NULL)
OR (notification_type='OCCASION_REMINDER' AND destination_kind='OCCASION' AND status IS
NULL)
OR (notification_type='WALLET_TOPUP_COMPLETED' AND destination_kind='WALLET' AND status IS
NULL)
OR (notification_type='WITHDRAWAL_STATUS_CHANGED' AND destination_kind='WALLET'
AND status IS NOT NULL AND status IN ('REQUESTED','APPROVED','REJECTED','PAID','SUBMITTED')))
)
        """
    )
    op.execute(
        """
CREATE INDEX idx_inbox_owned_time ON inbox_notifications(user_id,created_at DESC,id DESC)
        """
    )
    op.execute(
        """
CREATE INDEX idx_inbox_unread ON inbox_notifications(user_id,created_at DESC,id DESC) WHERE
read_at IS NULL
        """
    )
    op.execute(
        """
CREATE TABLE order_timeline_origins (order_id uuid PRIMARY KEY REFERENCES orders(id) ON
DELETE CASCADE)
        """
    )
    op.execute(
        """
CREATE TABLE order_timeline_events (
id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id uuid NOT NULL REFERENCES orders(id)
ON DELETE CASCADE,
status varchar(32) NOT NULL CHECK(status IN
('NEW','ASSIGNED','WAITING_PAYMENT','IN_PROGRESS','DELIVERED','COMPLETED','CANCELLED','DISPUTED','REFUNDED')),
actor_role varchar(16) CHECK(actor_role IN ('CUSTOMER','COURIER','ADMIN')),
created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL
DEFAULT clock_timestamp())
        """
    )
    op.execute(
        """
CREATE INDEX idx_order_timeline_time ON order_timeline_events(order_id,created_at,id)
        """
    )
    op.execute(
        """
CREATE TABLE occasion_reminders (
occasion_id uuid PRIMARY KEY REFERENCES occasions(id) ON DELETE CASCADE,
version integer NOT NULL CHECK(version > 0), scheduled_at timestamptz NOT NULL, enabled
boolean NOT NULL,
delivered_at timestamptz, push_completed_at timestamptz, push_attempts integer NOT NULL
DEFAULT 0 CHECK(push_attempts BETWEEN 0 AND 5),
next_attempt_at timestamptz, lease_token uuid, lease_expires_at timestamptz, cursor_token_id
uuid)
        """
    )
    op.execute(
        """
CREATE INDEX idx_occasion_reminders_due ON occasion_reminders(scheduled_at,occasion_id)
WHERE enabled AND delivered_at IS NULL
        """
    )
    op.execute(
        """
CREATE INDEX idx_occasion_reminders_push ON occasion_reminders(next_attempt_at,occasion_id)
WHERE enabled AND delivered_at IS NOT NULL AND push_completed_at IS NULL
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_inbox_event(recipient uuid, dedup text, event_type text,
kind text, resource uuid, event_status text DEFAULT NULL) RETURNS void LANGUAGE plpgsql AS
$$
BEGIN
INSERT INTO
inbox_notifications(user_id,event_key,notification_type,destination_kind,resource_id,status)
VALUES(recipient,dedup,event_type,kind,resource,event_status) ON CONFLICT(user_id,event_key)
DO NOTHING;
END $$
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_order_mobile_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_id uuid; category text;
BEGIN
IF TG_OP='UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
IF TG_OP='INSERT' THEN INSERT INTO order_timeline_origins(order_id) VALUES(NEW.id); END IF;
category := NULLIF(current_setting('giftly.audit_category',true),'');
IF category NOT IN ('CUSTOMER','COURIER','ADMIN') OR
NULLIF(current_setting('giftly.audit_actor_id',true),'') IS NULL THEN category := NULL; END
IF;
INSERT INTO order_timeline_events(order_id,status,actor_role)
VALUES(NEW.id,NEW.status::text,category) RETURNING id INTO event_id;
PERFORM
giftly_inbox_event(NEW.customer_id,'order:'||event_id,'ORDER_STATUS_CHANGED','ORDER',NEW.id,NEW.status::text);
IF NEW.courier_id IS NOT NULL AND NEW.courier_id <> NEW.customer_id THEN
PERFORM
giftly_inbox_event(NEW.courier_id,'order:'||event_id,'ORDER_STATUS_CHANGED','ORDER',NEW.id,NEW.status::text);
END IF;
RETURN NEW;
END $$
        """
    )
    op.execute(
        """
CREATE TRIGGER order_mobile_event AFTER INSERT OR UPDATE OF status ON orders
FOR EACH ROW EXECUTE FUNCTION giftly_order_mobile_event()
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_invoice_mobile_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE customer uuid; courier uuid;
BEGIN
IF NEW.status::text <> 'ISSUED' THEN RETURN NEW; END IF;
IF TG_OP='UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
SELECT customer_id,courier_id INTO customer,courier FROM orders WHERE id=NEW.order_id;
PERFORM
giftly_inbox_event(customer,'invoice:'||NEW.id||'-issued','INVOICE_ISSUED','ORDER',NEW.order_id);
IF courier IS NOT NULL AND courier <> customer THEN
PERFORM
giftly_inbox_event(courier,'invoice:'||NEW.id||'-issued','INVOICE_ISSUED','ORDER',NEW.order_id);
END IF;
RETURN NEW;
END $$
        """
    )
    op.execute(
        """
CREATE TRIGGER invoice_mobile_event AFTER INSERT OR UPDATE OF status ON invoices
FOR EACH ROW EXECUTE FUNCTION giftly_invoice_mobile_event()
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_payment_mobile_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF NEW.purpose::text='WALLET_TOPUP' AND NEW.status::text='PAID' THEN
IF TG_OP='INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
PERFORM
giftly_inbox_event(NEW.user_id,'topup:'||NEW.id,'WALLET_TOPUP_COMPLETED','WALLET',NEW.id);
END IF;
END IF;
RETURN NEW;
END $$
        """
    )
    op.execute(
        """
CREATE TRIGGER payment_mobile_event AFTER INSERT OR UPDATE OF status ON payment_intents
FOR EACH ROW EXECUTE FUNCTION giftly_payment_mobile_event()
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_withdrawal_mobile_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
PERFORM giftly_inbox_event(NEW.courier_id,'withdrawal:'||gen_random_uuid(),
'WITHDRAWAL_STATUS_CHANGED','WALLET',NEW.id,NEW.status::text);
END IF;
RETURN NEW;
END $$
        """
    )
    op.execute(
        """
CREATE TRIGGER withdrawal_mobile_event AFTER INSERT OR UPDATE OF status ON withdrawals
FOR EACH ROW EXECUTE FUNCTION giftly_withdrawal_mobile_event()
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_occasion_reminder_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='UPDATE' AND (NEW.occasion_date,NEW.reminder_days_before,NEW.reminder_enabled)
IS NOT DISTINCT FROM (OLD.occasion_date,OLD.reminder_days_before,OLD.reminder_enabled) THEN
NEW.reminder_version := OLD.reminder_version;
RETURN NEW;
END IF;
IF TG_OP='UPDATE' THEN NEW.reminder_version := OLD.reminder_version+1;
ELSE NEW.reminder_version := 1; END IF;
RETURN NEW;
END $$
        """
    )
    op.execute(
        """
CREATE TRIGGER occasion_reminder_version BEFORE INSERT OR UPDATE ON occasions
FOR EACH ROW EXECUTE FUNCTION giftly_occasion_reminder_schedule()
        """
    )
    op.execute(
        """
CREATE FUNCTION giftly_occasion_reminder_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='UPDATE' AND NEW.reminder_version=OLD.reminder_version THEN RETURN NEW; END IF;
INSERT INTO occasion_reminders(occasion_id,version,scheduled_at,enabled)
VALUES(NEW.id,NEW.reminder_version,
(NEW.occasion_date-NEW.reminder_days_before+time '09:00') AT TIME ZONE 'Asia/Riyadh',
NEW.reminder_enabled)
ON CONFLICT(occasion_id) DO UPDATE SET
version=EXCLUDED.version,scheduled_at=EXCLUDED.scheduled_at,enabled=EXCLUDED.enabled,
delivered_at=NULL,push_completed_at=NULL,push_attempts=0,next_attempt_at=NULL,
lease_token=NULL,lease_expires_at=NULL,cursor_token_id=NULL;
RETURN NEW;
END $$
        """
    )
    op.execute(
        """
CREATE TRIGGER occasion_reminder_outbox AFTER INSERT OR UPDATE ON occasions
FOR EACH ROW EXECUTE FUNCTION giftly_occasion_reminder_outbox()
        """
    )


def downgrade() -> None:
    """Refuse data loss before removing empty capture tables and opt-in fields."""
    op.execute(
        "LOCK TABLE orders, invoices, payment_intents, withdrawals, occasions, "
        "inbox_notifications, order_timeline_events, order_timeline_origins, "
        "occasion_reminders IN SHARE ROW EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM inbox_notifications "
            "UNION ALL SELECT 1 FROM order_timeline_events "
            "UNION ALL SELECT 1 FROM order_timeline_origins "
            "UNION ALL SELECT 1 FROM occasion_reminders WHERE enabled "
            "UNION ALL SELECT 1 FROM occasions WHERE reminder_enabled)"
        )
    ):
        raise RuntimeError(
            "Preserve mobile inbox/status history and opted-in reminders before rollback."
        )
    op.execute("DROP TRIGGER occasion_reminder_outbox ON occasions")
    op.execute("DROP TRIGGER occasion_reminder_version ON occasions")
    op.execute("DROP FUNCTION giftly_occasion_reminder_outbox()")
    op.execute("DROP FUNCTION giftly_occasion_reminder_schedule()")
    op.execute("DROP TRIGGER withdrawal_mobile_event ON withdrawals")
    op.execute("DROP FUNCTION giftly_withdrawal_mobile_event()")
    op.execute("DROP TRIGGER payment_mobile_event ON payment_intents")
    op.execute("DROP FUNCTION giftly_payment_mobile_event()")
    op.execute("DROP TRIGGER invoice_mobile_event ON invoices")
    op.execute("DROP FUNCTION giftly_invoice_mobile_event()")
    op.execute("DROP TRIGGER order_mobile_event ON orders")
    op.execute("DROP FUNCTION giftly_order_mobile_event()")
    op.execute("DROP FUNCTION giftly_inbox_event(uuid,text,text,text,uuid,text)")
    op.execute("DROP TABLE occasion_reminders")
    op.execute("DROP TABLE order_timeline_events")
    op.execute("DROP TABLE order_timeline_origins")
    op.execute("DROP TABLE inbox_notifications")
    op.execute(
        "ALTER TABLE occasions DROP CONSTRAINT chk_occasions_reminder_version, DROP COLUMN reminder_version, DROP COLUMN reminder_enabled"
    )
