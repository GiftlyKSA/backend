"""Restart reminder schedules when an occasion changes owner."""

from alembic import op

revision = "0044_reminder_owner_fence"
down_revision = "0043_receipt_retry"
branch_labels = None
depends_on = None


def _replace_schedule(*, track_owner: bool) -> None:
    owner_check = "AND NEW.user_id IS NOT DISTINCT FROM OLD.user_id" if track_owner else ""
    op.execute(f"""
CREATE OR REPLACE FUNCTION giftly_occasion_reminder_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP='UPDATE' AND (NEW.occasion_date,NEW.reminder_days_before,NEW.reminder_enabled,NEW.recurrence)
IS NOT DISTINCT FROM (OLD.occasion_date,OLD.reminder_days_before,OLD.reminder_enabled,OLD.recurrence)
{owner_check} THEN
NEW.reminder_version := OLD.reminder_version; RETURN NEW; END IF;
IF TG_OP='UPDATE' THEN NEW.reminder_version := OLD.reminder_version+1;
ELSE NEW.reminder_version := 1; END IF;
RETURN NEW;
END $$
""")


def upgrade() -> None:
    """Version future owner changes so the outbox trigger resets each affected lease."""
    _replace_schedule(track_owner=True)


def downgrade() -> None:
    """Restore the prior trigger predicate while preserving current schedule state."""
    _replace_schedule(track_owner=False)
