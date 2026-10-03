"""Durable customer-scoped idempotency for invoice repricing."""

import hashlib
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InvoicePromoOperation


class InvoicePromoRepository:
    """Keep operation receipts in the same transaction as invoice changes."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the caller's transaction."""
        self._session = session

    def savepoint(self) -> AbstractAsyncContextManager[object]:
        """Roll back failed replacements without altering the existing invoice."""
        return self._session.begin_nested()

    async def lock_key(self, customer_id: UUID, key: str) -> None:
        """Serialize a customer's operation key across API workers until commit."""
        digest = hashlib.sha256(f"invoice-promo:{customer_id}:{key}".encode()).digest()
        lock_id = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})

    async def get(self, customer_id: UUID, key: str) -> InvoicePromoOperation | None:
        """Read only this customer's receipt."""
        receipt: InvoicePromoOperation | None = await self._session.scalar(
            select(InvoicePromoOperation).where(
                InvoicePromoOperation.customer_id == customer_id,
                InvoicePromoOperation.idempotency_key == key,
            )
        )
        return receipt

    async def record(
        self, customer_id: UUID, key: str, invoice_id: UUID, code: str | None, result_id: UUID
    ) -> None:
        """Persist a successful no-op or replacement before the transaction commits."""
        self._session.add(
            InvoicePromoOperation(
                customer_id=customer_id,
                idempotency_key=key,
                invoice_id=invoice_id,
                code=code,
                result_invoice_id=result_id,
            )
        )
        await self._session.flush()
