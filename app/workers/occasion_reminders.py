"""Recover opted-in one-off occasion reminders every minute."""

from app.services.notification_reminder_service import send_occasion_reminders
from app.workers.broker import broker
from app.workers.delivery_budget import run_delivery_drainer


@broker.task(schedule=[{"cron": "* * * * *"}])
async def deliver_occasion_reminders() -> None:
    """Bound each run while keeping inbox delivery independent of push success."""
    await run_delivery_drainer(
        "occasion reminders", lambda deadline: send_occasion_reminders(deadline=deadline)
    )
