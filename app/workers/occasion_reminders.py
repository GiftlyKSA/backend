"""Recover opted-in one-off occasion reminders every minute."""

from app.services.notification_reminder_service import send_occasion_reminders
from app.workers.broker import broker


@broker.task(schedule=[{"cron": "* * * * *"}])
async def deliver_occasion_reminders() -> None:
    """Bound each run while keeping inbox delivery independent of push success."""
    await send_occasion_reminders()
