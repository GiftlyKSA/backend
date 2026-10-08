"""Durable, at-least-once delivery of paid-invoice receipts."""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.money import money_str
from app.integrations.email.base import EmailClient
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.user_repository import UserRepository
from app.services.invoice_pdf import render_invoice_pdf

_DEFAULT_TEMPLATE_KEY = "invoice_paid_receipt"
_SEND_TIMEOUT_SECONDS = 15
_CLAIM_LEASE = timedelta(seconds=60)


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
        A stable provider idempotency key makes retries replay-safe within its window.
        """
        token = uuid.uuid4()
        async with self._factory() as session:
            invoices = InvoiceRepository(session)
            invoice = await invoices.claim_receipt(
                invoice_id, token=token, now=datetime.now(UTC), lease=_CLAIM_LEASE
            )
            if invoice is None:
                return False
            order = await OrderRepository(session).get(invoice.order_id)
            customer = await UserRepository(session).get(order.customer_id) if order else None
            address = customer.email if customer else None
            items = await invoices.list_items(invoice.id) if address else []
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

        if not address:
            return False
        pdf = await asyncio.to_thread(render_invoice_pdf, invoice, items)
        variables["pdf_base64"] = base64.b64encode(pdf).decode("ascii")
        template = self._settings.SNDR_INVOICE_PAID_TEMPLATE_KEY or _DEFAULT_TEMPLATE_KEY
        async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
            await self._email.send_transactional(address, template, variables)

        async with self._factory() as session:
            completed = await InvoiceRepository(session).complete_receipt(
                invoice_id, token=token, when=datetime.now(UTC)
            )
            await session.commit()
        return bool(address and completed)
