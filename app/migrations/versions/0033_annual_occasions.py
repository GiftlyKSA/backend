"""Opt-in annual Gregorian dates; February 29 occurs only in leap years."""

import sqlalchemy as sa
from alembic import op

revision = "0033_annual_occasions"
down_revision = "0032_courier_avatar_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep one-off schedules unchanged and fence annual progress in the existing outbox."""
    op.execute(
        "ALTER TABLE occasions ADD COLUMN recurrence varchar(8) NOT NULL DEFAULT 'NONE', ADD CONSTRAINT chk_occasions_recurrence CHECK(recurrence IN ('NONE','ANNUAL'))"
    )
    op.execute("ALTER TABLE occasion_reminders ADD COLUMN occurrence_date date")
    op.execute(
        "UPDATE occasion_reminders r SET occurrence_date=o.occasion_date FROM occasions o WHERE o.id=r.occasion_id"
    )
    op.execute("ALTER TABLE occasion_reminders ALTER COLUMN occurrence_date SET NOT NULL")
    op.execute("""
CREATE FUNCTION giftly_next_annual_date(anchor date, lead_days integer, reference_at timestamptz)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE y integer; candidate date;
BEGIN
FOR y IN greatest(extract(year FROM anchor)::integer,extract(year FROM reference_at AT TIME ZONE 'Asia/Riyadh')::integer)..least(9999,greatest(extract(year FROM anchor)::integer,extract(year FROM reference_at AT TIME ZONE 'Asia/Riyadh')::integer)+8) LOOP
IF extract(month FROM anchor)=2 AND extract(day FROM anchor)=29 AND NOT (y%4=0 AND (y%100<>0 OR y%400=0)) THEN CONTINUE; END IF;
candidate := make_date(y,extract(month FROM anchor)::integer,extract(day FROM anchor)::integer);
IF candidate>=anchor AND (candidate-lead_days+time '09:00') AT TIME ZONE 'Asia/Riyadh'>=reference_at THEN RETURN candidate; END IF;
END LOOP;
RETURN NULL;
END $$
""")
    op.execute("""
CREATE OR REPLACE FUNCTION giftly_occasion_reminder_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='UPDATE' AND (NEW.occasion_date,NEW.reminder_days_before,NEW.reminder_enabled,NEW.recurrence)
IS NOT DISTINCT FROM (OLD.occasion_date,OLD.reminder_days_before,OLD.reminder_enabled,OLD.recurrence) THEN
NEW.reminder_version := OLD.reminder_version; RETURN NEW; END IF;
IF TG_OP='UPDATE' THEN NEW.reminder_version := OLD.reminder_version+1;
ELSE NEW.reminder_version := 1; END IF;
RETURN NEW;
END $$
""")
    op.execute("""
CREATE OR REPLACE FUNCTION giftly_occasion_reminder_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE occurrence date;
BEGIN
IF TG_OP='UPDATE' AND NEW.reminder_version=OLD.reminder_version THEN RETURN NEW; END IF;
occurrence := NEW.occasion_date;
IF NEW.recurrence='ANNUAL' THEN
occurrence := giftly_next_annual_date(NEW.occasion_date,NEW.reminder_days_before,clock_timestamp());
END IF;
INSERT INTO occasion_reminders(occasion_id,version,occurrence_date,scheduled_at,enabled)
VALUES(NEW.id,NEW.reminder_version,coalesce(occurrence,NEW.occasion_date),
(coalesce(occurrence,NEW.occasion_date)-NEW.reminder_days_before+time '09:00') AT TIME ZONE 'Asia/Riyadh',
NEW.reminder_enabled AND occurrence IS NOT NULL)
ON CONFLICT(occasion_id) DO UPDATE SET
version=EXCLUDED.version,occurrence_date=EXCLUDED.occurrence_date,scheduled_at=EXCLUDED.scheduled_at,enabled=EXCLUDED.enabled,
delivered_at=NULL,push_completed_at=NULL,push_attempts=0,next_attempt_at=NULL,
lease_token=NULL,lease_expires_at=NULL,cursor_token_id=NULL;
RETURN NEW;
END $$
""")


def downgrade() -> None:
    """Refuse loss of annual settings and restore the prior one-off functions."""
    op.execute("LOCK TABLE occasions,occasion_reminders IN SHARE ROW EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM occasions WHERE recurrence='ANNUAL')")
    ):
        raise RuntimeError("Preserve annual occasions before rollback.")
    op.execute("""
CREATE OR REPLACE FUNCTION giftly_occasion_reminder_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='UPDATE' AND (NEW.occasion_date,NEW.reminder_days_before,NEW.reminder_enabled)
IS NOT DISTINCT FROM (OLD.occasion_date,OLD.reminder_days_before,OLD.reminder_enabled) THEN
NEW.reminder_version := OLD.reminder_version; RETURN NEW; END IF;
IF TG_OP='UPDATE' THEN NEW.reminder_version := OLD.reminder_version+1;
ELSE NEW.reminder_version := 1; END IF;
RETURN NEW;
END $$
""")
    op.execute("""
CREATE OR REPLACE FUNCTION giftly_occasion_reminder_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='UPDATE' AND NEW.reminder_version=OLD.reminder_version THEN RETURN NEW; END IF;
INSERT INTO occasion_reminders(occasion_id,version,scheduled_at,enabled)
VALUES(NEW.id,NEW.reminder_version,
(NEW.occasion_date-NEW.reminder_days_before+time '09:00') AT TIME ZONE 'Asia/Riyadh',NEW.reminder_enabled)
ON CONFLICT(occasion_id) DO UPDATE SET version=EXCLUDED.version,scheduled_at=EXCLUDED.scheduled_at,
enabled=EXCLUDED.enabled,delivered_at=NULL,push_completed_at=NULL,push_attempts=0,
next_attempt_at=NULL,lease_token=NULL,lease_expires_at=NULL,cursor_token_id=NULL;
RETURN NEW;
END $$
""")
    op.execute("ALTER TABLE occasion_reminders DROP COLUMN occurrence_date")
    op.execute(
        "ALTER TABLE occasions DROP CONSTRAINT chk_occasions_recurrence, DROP COLUMN recurrence"
    )
    op.execute("DROP FUNCTION giftly_next_annual_date(date,integer,timestamptz)")
