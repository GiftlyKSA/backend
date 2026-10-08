"""TaskIQ broker wired to the Redis broker.

Background tasks (push, SMS, the invoice-paid receipt, reconciliation) are declared
against this broker so an HTTP handler never waits on a slow integration. Tasks are
added as each phase lands; the broker itself is the shared entry point.
"""

from __future__ import annotations

from importlib import import_module

from taskiq_redis import ListQueueBroker

from app.core.config import get_settings

_settings = get_settings()

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
    "chat_notifications",
    "expiry",
    "gateway_reconciliation",
    "media_cleanup",
    "order_notifications",
    "receipts",
    "reconciliation",
):
    import_module(f"app.workers.{_module}")
