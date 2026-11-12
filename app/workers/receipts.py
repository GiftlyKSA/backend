"""Invoice-paid receipt sweeper.

Drains due, unsent paid-invoice receipts in bounded sweeps. Failed sends are deferred or
exhausted durably. Each invoice has a time-bounded claim that overlapping sweeps skip.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_worker_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.email.base import EmailClient
from app.integrations.factory import build_email_client
from app.repositories.invoice_repository import InvoiceRepository
from app.services.receipt_service import RECEIPT_SEND_TIMEOUT_SECONDS, ReceiptService
from app.workers.broker import broker
from app.workers.delivery_budget import run_delivery_drainer

_logger = logging.getLogger("app.workers.receipts")


async def send_pending_receipts(
    *,
    limit: int = 100,
    email: EmailClient | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    deadline: float | None = None,
) -> int:
    """Send a batch of due paid-invoice receipts and return how many were sent.

    Collaborators are injectable for tests; by default the job builds its own engine and
    the environment's email client (a Fake outside production).
    """
    settings = settings or get_worker_settings()
    owned_client = build_email_client(settings) if email is None else None
    if owned_client is not None:
        email = owned_client
    assert email is not None
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)

    sent = 0
    try:
        async with factory() as session:
            invoice_ids = await InvoiceRepository(session).list_due_receipt_ids(limit)

        service = ReceiptService(factory=factory, email=email, settings=settings)
        loop = asyncio.get_running_loop()
        for invoice_id in invoice_ids:
            if deadline is not None and loop.time() + RECEIPT_SEND_TIMEOUT_SECONDS >= deadline:
                break
            try:
                if await service.send_receipt(invoice_id):
                    sent += 1
            except Exception:  # noqa: BLE001 - one bad invoice must not stall the sweep
                _logger.exception("receipt send failed for invoice %s", invoice_id)
    finally:
        try:
            if owned_client is not None:
                aclose = getattr(owned_client, "aclose", None)
                if aclose is not None:
                    await aclose()
        finally:
            if own_engine is not None:
                await own_engine.dispose()

    _logger.info("receipt sweep sent %d receipt(s)", sent)
    return sent


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def deliver_pending_receipts() -> None:
    """Deliver a bounded batch of due receipts on the existing cadence."""
    await run_delivery_drainer(
        "invoice receipts", lambda deadline: send_pending_receipts(deadline=deadline)
    )
