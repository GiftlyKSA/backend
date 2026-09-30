"""Invoice-paid receipt sweeper (SPEC SECTION 5.3, 21).

Drains the ``idx_invoices_receipt_pending`` set (PAID invoices with no receipt yet),
sending each customer their one receipt. The sweeper is the delivery mechanism, so a
receipt survives a failed send: it stays pending until a later pass delivers it. Each
invoice has a durable, time-bounded claim; overlapping sweeps skip claimed rows.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.integrations.email.base import EmailClient
from app.integrations.factory import build_clients
from app.repositories.invoice_repository import InvoiceRepository
from app.services.receipt_service import ReceiptService
from app.workers.audit import audited_system_job
from app.workers.broker import broker

_logger = logging.getLogger("app.workers.receipts")


async def send_pending_receipts(
    *,
    limit: int = 100,
    email: EmailClient | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
) -> int:
    """Send every pending paid-invoice receipt; returns how many were sent.

    Collaborators are injectable for tests; by default the job builds its own engine and
    the environment's email client (a Fake outside production).
    """
    settings = settings or get_settings()
    owned_clients = build_clients(settings) if email is None else None
    if owned_clients is not None:
        email = owned_clients.email
    assert email is not None
    own_engine = None
    if factory is None:
        own_engine = build_engine(settings)
        factory = build_session_factory(own_engine)

    sent = 0
    try:
        async with factory() as session:
            pending = await InvoiceRepository(session).list_receipt_pending(limit)
            invoice_ids = [invoice.id for invoice in pending]

        for invoice_id in invoice_ids:
            service = ReceiptService(factory=factory, email=email, settings=settings)
            try:
                if await service.send_receipt(invoice_id):
                    sent += 1
            except Exception:  # noqa: BLE001 - one bad invoice must not stall the sweep
                _logger.exception("receipt send failed for invoice %s", invoice_id)
    finally:
        try:
            if owned_clients is not None:
                for client in (
                    owned_clients.gateway,
                    owned_clients.email,
                    owned_clients.sms,
                    owned_clients.push,
                    owned_clients.storage,
                ):
                    aclose = getattr(client, "aclose", None)
                    if aclose is not None:
                        await aclose()
        finally:
            if own_engine is not None:
                await own_engine.dispose()

    _logger.info("receipt sweep sent %d receipt(s)", sent)
    return sent


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
@audited_system_job("deliver_pending_receipts")
async def deliver_pending_receipts() -> None:
    """Scheduled task: drain invoices not held by a live claim."""
    await send_pending_receipts()
