"""Schedule bounded, durable new-order push delivery."""

from __future__ import annotations

import logging

from app.services.order_notification_service import send_pending_order_notifications
from app.workers.broker import broker
from app.workers.delivery_budget import run_delivery_drainer

_logger = logging.getLogger("app.workers.order_notifications")


@broker.task(schedule=[{"cron": "* * * * *"}])
async def deliver_order_notifications() -> None:
    """Drain a bounded portion of the committed outbox every minute."""

    async def drain(deadline: float) -> None:
        sent = await send_pending_order_notifications(deadline=deadline)
        if sent:
            _logger.info("processed %d order notification page(s)", sent)

    await run_delivery_drainer("order notifications", drain)
