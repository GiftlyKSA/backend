"""Schedule bounded delivery of durable chat push intents."""

from app.services.chat_notification_service import send_pending_chat_notifications
from app.workers.broker import broker


@broker.task(schedule=[{"cron": "* * * * *"}])
async def deliver_chat_notifications() -> None:
    """Recover committed chat pushes every minute, including after process restarts."""
    await send_pending_chat_notifications()
