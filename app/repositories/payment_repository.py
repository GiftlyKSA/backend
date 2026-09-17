"""Payment-intent and wallet-top-up persistence (SPEC SECTION 5.1, ADR 0003).

A single ``payment_intents`` row is the only gateway-facing record, discriminated by
``purpose``. The webhook does ONE lookup by ``gateway_reference`` and dispatches on
purpose — the ambiguity that produces double-credits is designed out.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DhamenNotificationReceipt,
    PaymentIntent,
    PayoutTransfer,
    WalletTopup,
)
from app.models.enums import PaymentIntentStatus, PaymentPurpose


class PaymentRepository:
    """Creates and reads payment intents and top-ups, with FOR UPDATE on settle."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def create_intent(
        self,
        *,
        user_id: uuid.UUID,
        purpose: PaymentPurpose,
        amount: Decimal,
        reference_invoice_id: uuid.UUID | None,
        expires_at: datetime,
    ) -> PaymentIntent:
        """Insert a NEW payment intent for a top-up or an invoice remainder."""
        intent = PaymentIntent(
            user_id=user_id,
            purpose=purpose,
            amount=amount,
            status=PaymentIntentStatus.NEW,
            reference_invoice_id=reference_invoice_id,
            expires_at=expires_at,
        )
        self._session.add(intent)
        await self._session.flush()
        return intent

    async def attach_simulated_checkout(
        self, intent: PaymentIntent, *, payment_link_id: str, url: str
    ) -> None:
        """Record simulated payment's payment-link ID and hosted checkout URL on the intent."""
        intent.checkout_provider = "SIMULATED"
        intent.gateway_reference = payment_link_id
        intent.gateway_payment_url = url
        await self._session.flush()

    async def create_topup(
        self,
        *,
        user_id: uuid.UUID,
        wallet_id: uuid.UUID,
        payment_intent_id: uuid.UUID,
        amount: Decimal,
    ) -> WalletTopup:
        """Insert the wallet-top-up row tied to its intent (1:1)."""
        topup = WalletTopup(
            user_id=user_id,
            wallet_id=wallet_id,
            payment_intent_id=payment_intent_id,
            amount=amount,
        )
        self._session.add(topup)
        await self._session.flush()
        return topup

    async def get_intent(self, intent_id: uuid.UUID) -> PaymentIntent | None:
        """Return a payment intent by id, or None."""
        return await self._session.get(PaymentIntent, intent_id)

    async def lock_intent_by_payment_link(self, payment_link_id: str) -> PaymentIntent | None:
        """Load a payment intent by Local payment simulation ID FOR UPDATE."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent)
            .where(
                PaymentIntent.checkout_provider == "SIMULATED",
                PaymentIntent.gateway_reference == payment_link_id,
            )
            .with_for_update()
        )
        return result

    async def lock_intent_by_gateway_reference(
        self, *, checkout_provider: str, gateway_reference: str
    ) -> PaymentIntent | None:
        """Load one provider-neutral payment intent by reference FOR UPDATE."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent)
            .where(
                PaymentIntent.checkout_provider == checkout_provider,
                PaymentIntent.gateway_reference == gateway_reference,
            )
            .with_for_update()
        )
        return result

    async def insert_notification_receipt_if_new(
        self,
        *,
        notification_id: str,
        batch_id: str,
        notification_type: str,
        payment_reference: str | None,
        transaction_id: str | None,
        raw_hash: str,
        processing_outcome: str = "PENDING",
    ) -> DhamenNotificationReceipt | None:
        """Insert an idempotency receipt, returning None for a replay."""
        statement = (
            insert(DhamenNotificationReceipt)
            .values(
                notification_id=notification_id,
                batch_id=batch_id,
                notification_type=notification_type,
                payment_reference=payment_reference,
                transaction_id=transaction_id,
                raw_hash=raw_hash,
                processing_outcome=processing_outcome,
            )
            .on_conflict_do_nothing(index_elements=[DhamenNotificationReceipt.notification_id])
            .returning(DhamenNotificationReceipt)
        )
        receipt = await self._session.scalar(statement)
        await self._session.flush()
        return receipt

    async def list_due_gateway_reconciliation(self, *, limit: int) -> list[PaymentIntent]:
        """Return unresolved generic gateway intents in deterministic oldest-first order."""
        return list(
            await self._session.scalars(
                select(PaymentIntent)
                .where(
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                    PaymentIntent.gateway_reference.is_not(None),
                )
                .order_by(PaymentIntent.created_at, PaymentIntent.id)
                .limit(limit)
            )
        )

    async def create_payout_transfer(
        self,
        *,
        withdrawal_id: uuid.UUID,
        provider: str,
        payment_reference: str,
        supplier_id: uuid.UUID,
        amount: Decimal,
        status: str,
    ) -> PayoutTransfer:
        """Create the single provider transfer record for a withdrawal."""
        transfer = PayoutTransfer(
            withdrawal_id=withdrawal_id,
            provider=provider,
            payment_reference=payment_reference,
            supplier_id=supplier_id,
            amount=amount,
            status=status,
        )
        self._session.add(transfer)
        await self._session.flush()
        return transfer

    async def lock_payout_transfer(self, withdrawal_id: uuid.UUID) -> PayoutTransfer | None:
        """Load a withdrawal's provider transfer FOR UPDATE."""
        result: PayoutTransfer | None = await self._session.scalar(
            select(PayoutTransfer)
            .where(PayoutTransfer.withdrawal_id == withdrawal_id)
            .with_for_update()
        )
        return result

    async def lock_payout_transfer_by_reference(
        self, *, provider: str, payment_reference: str
    ) -> PayoutTransfer | None:
        """Load a provider transfer by its provider-scoped reference FOR UPDATE."""
        result: PayoutTransfer | None = await self._session.scalar(
            select(PayoutTransfer)
            .where(
                PayoutTransfer.provider == provider,
                PayoutTransfer.payment_reference == payment_reference,
            )
            .with_for_update()
        )
        return result

    async def get_open_intent_for_invoice(self, invoice_id: uuid.UUID) -> PaymentIntent | None:
        """Return a still-NEW gateway intent for an invoice, or None (avoids duplicates)."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent).where(
                PaymentIntent.reference_invoice_id == invoice_id,
                PaymentIntent.status == PaymentIntentStatus.NEW,
            )
        )
        return result

    async def list_expired_new(self, *, now: datetime, limit: int) -> list[PaymentIntent]:
        """Return NEW intents whose expiry has passed (oldest first)."""
        return list(
            await self._session.scalars(
                select(PaymentIntent)
                .where(
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                    PaymentIntent.expires_at < now,
                )
                .order_by(PaymentIntent.expires_at)
                .limit(limit)
            )
        )

    async def lock_intent(self, intent_id: uuid.UUID) -> PaymentIntent | None:
        """Load a payment intent by id FOR UPDATE."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent).where(PaymentIntent.id == intent_id).with_for_update()
        )
        return result

    async def mark_expired(self, intent: PaymentIntent) -> None:
        """Transition a still-NEW intent to EXPIRED."""
        intent.status = PaymentIntentStatus.EXPIRED
        await self._session.flush()

    async def mark_paid(self, intent: PaymentIntent, *, paid_at: datetime) -> None:
        """Transition an intent to PAID, stamping the settlement time."""
        intent.status = PaymentIntentStatus.PAID
        intent.paid_at = paid_at
        await self._session.flush()

    async def mark_failed(self, intent: PaymentIntent, *, reason: str) -> None:
        """Transition an intent to FAILED, recording the reason."""
        intent.status = PaymentIntentStatus.FAILED
        intent.failure_reason = reason[:255]
        await self._session.flush()
