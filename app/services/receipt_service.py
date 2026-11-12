"""Durable, at-least-once delivery of paid-invoice receipts."""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.money import money_str
from app.integrations.email.base import EmailClient, EmailRecipientSuppressedError
from app.repositories.invoice_repository import InvoiceRepository
from app.services.invoice_pdf import render_invoice_pdf

_logger = logging.getLogger("app.receipts")
_DEFAULT_TEMPLATE_KEY = "invoice_paid_receipt"
RECEIPT_SEND_TIMEOUT_SECONDS = 15
_DELIVERY_TIMEOUT_SECONDS = 30
_CLAIM_LEASE = timedelta(seconds=60)
_FIRST_RETRY_DELAY = timedelta(minutes=15)
_MAX_RETRY_DELAY = timedelta(hours=4)
_MAX_FAILED_ATTEMPTS = 8
# SNDR replays a stable idempotency key for 24 hours after acceptance.
_REPLAY_WINDOW = timedelta(hours=23)


class ReceiptService:
    """Claims and sends receipts without holding a transaction over email."""

    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        email: EmailClient,
        settings: Settings,
    ) -> None:
        """Bind a session factory and email client."""
        self._factory = factory
        self._email = email
        self._settings = settings

    async def send_receipt(self, invoice_id: uuid.UUID) -> bool:
        """Send an eligible receipt and stamp only the claim this call owns.

        Provider acceptance followed by a crash or stamp failure can cause a retry.
        Expire ambiguous claims before the stable provider key can stop deduplicating.
        """
        token = uuid.uuid4()
        claimed_at = datetime.now(UTC)
        async with self._factory() as session:
            invoices = InvoiceRepository(session)
            claim = await invoices.claim_receipt(
                invoice_id, token=token, now=claimed_at, lease=_CLAIM_LEASE
            )
            if claim is None:
                return False
            invoice, address = claim
            first_attempt_at = invoice.receipt_first_attempt_at or claimed_at
            retry_deadline = first_attempt_at + _REPLAY_WINDOW
            failed_attempts = min(max(invoice.receipt_failed_attempts, 0), _MAX_FAILED_ATTEMPTS)
            if datetime.now(UTC) >= retry_deadline or failed_attempts >= _MAX_FAILED_ATTEMPTS:
                await invoices.finish_receipt_failure(
                    invoice_id,
                    token=token,
                    when=datetime.now(UTC),
                    failed_attempts=failed_attempts,
                    retry_at=None,
                )
                await session.commit()
                _logger.warning("Receipt retry budget exhausted for invoice %s.", invoice_id)
                return False
            items = await invoices.list_items(invoice.id)
            variables: dict[str, object] = {
                "invoice_id": str(invoice.id),
                "order_id": str(invoice.order_id),
                "currency": invoice.currency,
                "items_net_amount": money_str(invoice.items_net_amount),
                "courier_fee_amount": money_str(invoice.courier_fee_amount),
                "service_fee_amount": money_str(invoice.service_fee_amount),
                "discount_amount": money_str(invoice.discount_amount),
                "total_amount": money_str(invoice.total_amount),
                "promo_code": invoice.promo_code_snapshot or "",
                "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else "",
            }
            await session.commit()

        try:
            async with asyncio.timeout(_DELIVERY_TIMEOUT_SECONDS):
                pdf = await asyncio.to_thread(render_invoice_pdf, invoice, items)
                variables["pdf_base64"] = base64.b64encode(pdf).decode("ascii")
                template = self._settings.SNDR_INVOICE_PAID_TEMPLATE_KEY or _DEFAULT_TEMPLATE_KEY
                async with asyncio.timeout(RECEIPT_SEND_TIMEOUT_SECONDS):
                    await self._email.send_transactional(address, template, variables)
        except Exception as exc:
            await self._record_failure(
                invoice_id,
                token=token,
                failed_attempts=failed_attempts + 1,
                retry_deadline=retry_deadline,
                error=exc,
            )
            raise

        async with self._factory() as session:
            completed = await InvoiceRepository(session).complete_receipt(
                invoice_id, token=token, when=datetime.now(UTC)
            )
            await session.commit()
        return completed

    async def _record_failure(
        self,
        invoice_id: uuid.UUID,
        *,
        token: uuid.UUID,
        failed_attempts: int,
        retry_deadline: datetime,
        error: Exception,
    ) -> None:
        when = datetime.now(UTC)
        delay = min(_FIRST_RETRY_DELAY * 2 ** (failed_attempts - 1), _MAX_RETRY_DELAY)
        next_attempt_at = when + delay
        exhausted = (
            isinstance(error, EmailRecipientSuppressedError)
            or failed_attempts >= _MAX_FAILED_ATTEMPTS
            or next_attempt_at >= retry_deadline
        )
        async with self._factory() as session:
            recorded = await InvoiceRepository(session).finish_receipt_failure(
                invoice_id,
                token=token,
                when=when,
                failed_attempts=failed_attempts,
                retry_at=None if exhausted else next_attempt_at,
            )
            await session.commit()
        if recorded and exhausted:
            _logger.warning(
                "receipt retries exhausted for invoice %s (%s)", invoice_id, type(error).__name__
            )
