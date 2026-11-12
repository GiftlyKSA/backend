"""Best-effort Redis wake-ups for work already persisted in database outboxes."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger("app.worker_dispatch")
_WAKEUPS_KEY = "worker_wakeups"
_ENQUEUE_TIMEOUT_SECONDS = 0.5


class WorkerTask(StrEnum):
    """Worker drainers that may be requested after a transaction commits."""

    CHAT_LIVE_MESSAGES = "app.workers.chat_notifications:deliver_chat_live_messages"
    CHAT_NOTIFICATIONS = "app.workers.chat_notifications:deliver_chat_notifications"
    INVOICE_RECEIPTS = "app.workers.receipts:deliver_pending_receipts"
    ORDER_NOTIFICATIONS = "app.workers.order_notifications:deliver_order_notifications"
    PUSH_DELIVERIES = "app.workers.push_deliveries:deliver_push_notifications"


def request_worker_wakeup(session: AsyncSession, task: WorkerTask) -> None:
    """Remember a worker wake-up to publish after this session commits."""
    session.info.setdefault(_WAKEUPS_KEY, set()).add(task.value)


async def _enqueue_worker_task(task: WorkerTask) -> None:
    from app.workers.broker import broker

    registered_task = broker.find_task(task.value)
    if registered_task is None:
        raise LookupError("Worker task is not registered.")
    await registered_task.kiq()


async def dispatch_worker_wakeups(session: AsyncSession) -> set[WorkerTask]:
    """Enqueue pending wake-ups without making committed API writes fail on Redis errors."""
    task_names = session.info.pop(_WAKEUPS_KEY, set())
    tasks = sorted((task for task in WorkerTask if task.value in task_names), key=lambda t: t.value)
    queued: set[WorkerTask] = set()
    if not tasks:
        return queued
    try:
        async with asyncio.timeout(_ENQUEUE_TIMEOUT_SECONDS):
            for task in tasks:
                try:
                    await _enqueue_worker_task(task)
                    queued.add(task)
                except Exception as exc:  # noqa: BLE001 - scheduled sweepers recover durable work
                    _logger.warning(
                        "Worker wake-up failed for %s; scheduled recovery remains active (%s).",
                        task.value,
                        type(exc).__name__,
                    )
    except TimeoutError:
        _logger.warning("Worker wake-up budget elapsed; scheduled recovery remains active.")
    return queued
