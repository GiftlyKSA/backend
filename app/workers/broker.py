"""TaskIQ broker wired to the Redis broker (SPEC SECTION 2, 21).

Background tasks (push, SMS, the invoice-paid receipt, reconciliation) are declared
against this broker so an HTTP handler never waits on a slow integration. Tasks are
added as each phase lands; the broker itself is the shared entry point.
"""

from __future__ import annotations

from importlib import import_module

from taskiq_redis import ListQueueBroker

from app.core.config import get_settings

_settings = get_settings()

broker = ListQueueBroker(url=_settings.REDIS_URL.get_secret_value())

# Taskiq loads this broker entry point without discovering modules named outside tasks.py.
for _module in ("auto_approve", "expiry", "receipts", "reconciliation"):
    import_module(f"app.workers.{_module}")
