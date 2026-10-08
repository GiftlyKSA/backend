"""Payment-intent and wallet-top-up persistence.

A single ``payment_intents`` row is the only gateway-facing record, discriminated by
``purpose``. The webhook does ONE lookup by ``gateway_reference`` and dispatches on
purpose — the ambiguity that produces double-credits is designed out.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import mark_request_transaction, set_audit_actor
from app.core.db import emit_committed_audit_events
from app.models import (
    DhamenNotificationReceipt,
    Invoice,
    Order,
    PaymentIntent,
    PayoutTransfer,
    User,
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
        wallet_reserved_amount: Decimal = Decimal("0.00"),
        use_wallet: bool = True,
    ) -> PaymentIntent:
        """Insert a NEW payment intent for a top-up or an invoice remainder."""
        intent = PaymentIntent(
            user_id=user_id,
            purpose=purpose,
            amount=amount,
            status=PaymentIntentStatus.NEW,
            reference_invoice_id=reference_invoice_id,
            expires_at=expires_at,
            wallet_reserved_amount=wallet_reserved_amount,
            use_wallet=use_wallet,
            order_id=(
                await self._session.scalar(
                    select(Invoice.order_id).where(Invoice.id == reference_invoice_id)
                )
                if reference_invoice_id is not None
                else None
            ),
        )
        self._session.add(intent)
        await self._session.flush()
        return intent

    async def checkpoint(self) -> None:
        """Commit durable coordination before releasing locks for provider HTTP calls."""
        await self._session.commit()
        emit_committed_audit_events(self._session)

    async def resume_actor(self) -> None:
        """Restore transaction-local auditing after a financial coordination commit."""
        category = self._session.info.get("audit_actor_category", "SYSTEM")
        actor_id = self._session.info.get("audit_actor_id")
        if category != "SYSTEM":
            await mark_request_transaction(self._session)
        await set_audit_actor(self._session, category=category, actor_user_id=actor_id)

    async def flush(self) -> None:
        """Flush session state without committing the caller's settlement transaction."""
        await self._session.flush()

    async def lock_session_intent(self, intent_id: uuid.UUID) -> PaymentIntent | None:
        """Lock invoice then order then intent, matching checkout and financial operations."""
        candidate = await self.get_intent(intent_id)
        if candidate is None:
            return None
        if candidate.reference_invoice_id is not None:
            await self._session.scalar(
                select(Invoice)
                .where(Invoice.id == candidate.reference_invoice_id)
                .with_for_update()
            )
        if candidate.order_id is not None:
            await self._session.scalar(
                select(Order).where(Order.id == candidate.order_id).with_for_update()
            )
        return await self.lock_intent(intent_id)

    async def lock_session_batch(
        self, intent_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, PaymentIntent]:
        """Lock each resource class in order before any batch wallet settlement."""
        ids = sorted(set(intent_ids))
        if not ids:
            return {}
        if len(ids) > 100:
            raise ValueError("Payment batch exceeds its reference limit.")
        rows = (
            await self._session.execute(
                select(PaymentIntent.reference_invoice_id, PaymentIntent.order_id)
                .where(PaymentIntent.id.in_(ids))
                .limit(100)
            )
        ).all()
        invoice_ids = sorted({row[0] for row in rows if row[0] is not None})
        order_ids = sorted({row[1] for row in rows if row[1] is not None})
        if invoice_ids:
            await self._session.execute(
                select(Invoice.id)
                .where(Invoice.id.in_(invoice_ids))
                .order_by(Invoice.id)
                .with_for_update()
            )
        if order_ids:
            await self._session.execute(
                select(Order.id).where(Order.id.in_(order_ids)).order_by(Order.id).with_for_update()
            )
        locked = await self._session.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.id.in_(ids))
            .order_by(PaymentIntent.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {intent.id: intent for intent in locked}

    async def get_open_intent_for_order(self, order_id: uuid.UUID) -> PaymentIntent | None:
        """Find the single open attempt across all invoice revisions of an order."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent)
            .where(
                PaymentIntent.order_id == order_id,
                or_(
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                    PaymentIntent.checkout_state == "REVIEW",
                ),
            )
            .order_by((PaymentIntent.checkout_state == "REVIEW").desc())
            .limit(1)
        )
        return result

    async def get_topup_for_actor(
        self, user_id: uuid.UUID, *, open_only: bool = False
    ) -> PaymentIntent | None:
        """Recover a payer's hosted top-up, preferring unresolved attempts."""
        query = select(PaymentIntent).where(
            PaymentIntent.user_id == user_id,
            PaymentIntent.purpose == PaymentPurpose.WALLET_TOPUP,
            PaymentIntent.checkout_provider != "SIMULATED",
        )
        if open_only:
            query = query.where(
                or_(
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                    PaymentIntent.checkout_state == "REVIEW",
                )
            )
        result: PaymentIntent | None = await self._session.scalar(
            query.order_by(
                (PaymentIntent.checkout_state == "REVIEW").desc(),
                (PaymentIntent.status == PaymentIntentStatus.NEW).desc(),
                PaymentIntent.created_at.desc(),
                PaymentIntent.id.desc(),
            ).limit(1)
        )
        return result

    async def lock_topup_owner(self, user_id: uuid.UUID) -> None:
        """Serialize creation without taking wallet locks before the ledger lock order."""
        await self._session.scalar(select(User.id).where(User.id == user_id).with_for_update())

    async def get_intent_for_actor(
        self, intent_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentIntent | None:
        """Return only the authenticated payer's own session."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent).where(
                PaymentIntent.id == intent_id,
                PaymentIntent.user_id == user_id,
            )
        )
        return result

    async def get_latest_intent_for_order(
        self,
        *,
        order_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> PaymentIntent | None:
        """Recover an owned checkout after a lost response, preferring the active attempt."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent)
            .where(
                PaymentIntent.order_id == order_id,
                PaymentIntent.user_id == user_id,
            )
            .order_by(
                (PaymentIntent.checkout_state == "REVIEW").desc(),
                (PaymentIntent.status == PaymentIntentStatus.NEW).desc(),
                PaymentIntent.created_at.desc(),
                PaymentIntent.id.desc(),
            )
            .limit(1)
        )
        return result

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
            .execution_options(populate_existing=True)
        )
        return result

    async def get_intent_by_payment_link(self, payment_link_id: str) -> PaymentIntent | None:
        """Find the invoice reference before acquiring invoice-first settlement locks."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent).where(
                PaymentIntent.checkout_provider == "SIMULATED",
                PaymentIntent.gateway_reference == payment_link_id,
            )
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

    async def list_pending_hosted(self, *, provider: str, limit: int) -> list[PaymentIntent]:
        """Rotate pending attempts by last check to prevent backlog starvation."""
        return list(
            await self._session.scalars(
                select(PaymentIntent)
                .where(
                    PaymentIntent.checkout_provider == provider,
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                    PaymentIntent.checkout_state != "REVIEW",
                )
                .order_by(
                    PaymentIntent.checkout_checked_at.asc().nullsfirst(),
                    PaymentIntent.created_at,
                    PaymentIntent.id,
                )
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

    async def get_open_intent_for_invoice(
        self, invoice_id: uuid.UUID, *, for_update: bool = False
    ) -> PaymentIntent | None:
        """Return a still-NEW gateway intent for an invoice, or None (avoids duplicates)."""
        statement = select(PaymentIntent).where(
            PaymentIntent.reference_invoice_id == invoice_id,
            PaymentIntent.status == PaymentIntentStatus.NEW,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        result: PaymentIntent | None = await self._session.scalar(statement)
        return result

    async def list_expired_new(self, *, now: datetime, limit: int) -> list[PaymentIntent]:
        """Return expired NEW top-ups; invoice attempts have a separate expiry policy."""
        return list(
            await self._session.scalars(
                select(PaymentIntent)
                .where(
                    PaymentIntent.status == PaymentIntentStatus.NEW,
                    PaymentIntent.purpose == PaymentPurpose.WALLET_TOPUP,
                    PaymentIntent.expires_at < now,
                )
                .order_by(PaymentIntent.expires_at)
                .limit(limit)
            )
        )

    async def lock_intent(self, intent_id: uuid.UUID) -> PaymentIntent | None:
        """Load a payment intent by id FOR UPDATE."""
        result: PaymentIntent | None = await self._session.scalar(
            select(PaymentIntent)
            .where(PaymentIntent.id == intent_id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
