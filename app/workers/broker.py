"""TaskIQ broker wired to the Redis broker.

The separate scheduler publishes scheduled jobs to Redis; separate consumers execute
them. API services persist durable pending work without starting this broker's consumers
or scheduler. Request-specific SMS verification remains part of the authenticated flow.
"""

from __future__ import annotations

import os
from importlib import import_module

from taskiq_redis import ListQueueBroker

from app.core.config import get_scheduler_settings, get_worker_settings
from app.core.logging import configure_logging

configure_logging(os.getenv("LOG_LEVEL", "INFO"))
_component = os.getenv("GIFTLY_TASKIQ_COMPONENT", "worker")
if _component not in ("worker", "scheduler"):
    raise RuntimeError("Invalid internal Taskiq component.")
_settings = get_scheduler_settings() if _component == "scheduler" else get_worker_settings()

broker = ListQueueBroker(
    url=_settings.REDIS_URL.get_secret_value(),
    max_connection_pool_size=100,
    timeout=5,
    socket_connect_timeout=5,
    # The worker's BRPOP intentionally waits for jobs without a read deadline.
    # Ordinary application Redis commands use the bounded client in core.redis.
    socket_timeout=None,
)

# Taskiq loads this broker entry point without discovering modules named outside tasks.py.
for _module in (
    "auto_approve",
    "occasion_reminders",
    "account_requests",
    "chat_notifications",
    "expiry",
    "gateway_reconciliation",
    "media_cleanup",
    "order_notifications",
    "operations",
    "receipts",
    "reconciliation",
):
    import_module(f"app.workers.{_module}")
