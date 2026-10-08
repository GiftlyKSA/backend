"""Durable checkout creation, reconciliation and closure outside HTTP transactions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.core.exceptions import NotFoundError, PaymentSessionPendingError
from app.integrations.payments.base import (
    PaymentClient,
    PaymentContext,
    PaymentState,
    PaymentStatus,
)
from app.models import PaymentIntent
from app.models.enums import PaymentIntentStatus
from app.repositories.payment_repository import PaymentRepository
from app.services.payment_reservation_service import PaymentReservationService


class HostedPaymentService:
    """Coordinate the provider lifecycle; database constraints remain the authority."""

    def __init__(
        self,
        *,
        payments: PaymentRepository,
        gateway: PaymentClient,
        reservations: PaymentReservationService,
        settle: Callable[[PaymentIntent], Awaitable[None]],
    ) -> None:
        """Compose the provider, persistence and shared settlement rules."""
        self._payments = payments
        self._gateway = gateway
        self._reservations = reservations
        self._settle = settle

    def context(self, intent: PaymentIntent) -> PaymentContext:
        """Require the immutable snapshot to match the intent before any provider call."""
        if intent.checkout_snapshot is None or intent.checkout_provider != self._gateway.provider:
            raise PaymentSessionPendingError()
        context = PaymentContext.model_validate(intent.checkout_snapshot)
        if (
            context.reference != intent.gateway_reference
            or context.reference != str(intent.id)
            or context.customer.external_id != str(intent.user_id)
            or context.amount != intent.amount
            or context.currency != intent.currency
            or context.amount_from_wallet != intent.wallet_reserved_amount
        ):
            raise PaymentSessionPendingError()
        return context

    async def _lock(self, intent: PaymentIntent) -> PaymentIntent:
        await self._payments.resume_actor()
        current = await self._payments.lock_session_intent(intent.id)
        if current is None:
            raise NotFoundError("Payment session not found.")
        return current

    async def create(self, intent: PaymentIntent, context: PaymentContext) -> PaymentIntent:
        """Commit the claim once; an ambiguous create is recovered through status lookup."""
        self._gateway.validate_checkout(context)
        intent.checkout_provider = self._gateway.provider
        intent.gateway_reference = context.reference
        intent.gateway_customer_identifier = context.customer.external_id
        intent.checkout_state = "CREATING"
        intent.checkout_snapshot = context.model_dump(mode="json")
        await self._payments.checkpoint()
        checkout = await self._gateway.create_checkout(context)
        current = await self._lock(intent)
        if current.status is PaymentIntentStatus.NEW and current.checkout_state == "CREATING":
            current.gateway_payment_url = checkout.payment_url
            current.checkout_state = "ACTIVE"
            await self._payments.flush()
        return current

    async def reuse_or_close(self, intent: PaymentIntent) -> PaymentIntent | None:
        """Reuse an active checkout; replace it only after confirmed remote closure."""
        if intent.checkout_state == "REVIEW":
            raise PaymentSessionPendingError("This order payment requires review.")
        if (
            intent.checkout_state == "ACTIVE"
            and intent.gateway_payment_url
            and intent.expires_at > datetime.now(UTC)
        ):
            return intent
        current = await self.reconcile(intent)
        if current.status is PaymentIntentStatus.PAID:
            return current
        if current.expires_at > datetime.now(UTC) and current.checkout_state == "ACTIVE":
            return current
        if current.checkout_state == "CREATING":
            raise PaymentSessionPendingError()
        closed = await self.close(current)
        if closed.status is PaymentIntentStatus.PAID:
            return closed
        return None

    async def reconcile(self, intent: PaymentIntent) -> PaymentIntent:
        """Use the credentialed provider response, never a client redirect or callback amount."""
        status = await self.verify_status(intent)
        try:
            return await self.apply_status(intent, status)
        except PaymentSessionPendingError:
            if intent.checkout_state == "REVIEW":
                await self._payments.checkpoint()
            raise

    async def verify_status(self, intent: PaymentIntent) -> PaymentStatus | None:
        """Query the provider before acquiring any settlement locks."""
        if intent.status is PaymentIntentStatus.PAID:
            return None
        context = self.context(intent)
        intent.checkout_checked_at = datetime.now(UTC)
        await self._payments.checkpoint()
        return await self._gateway.get_payment_status(context)

    async def apply_status(
        self, intent: PaymentIntent, status: PaymentStatus | None
    ) -> PaymentIntent:
        """Apply verified state under locks without committing the caller's transaction."""
        return await self.apply_locked_status(await self._lock(intent), status)

    async def apply_locked_status(
        self, current: PaymentIntent, status: PaymentStatus | None
    ) -> PaymentIntent:
        """Apply verified state to an intent already locked by the caller's batch."""
        if current.status is PaymentIntentStatus.PAID:
            return current
        if status is None:
            raise PaymentSessionPendingError()
        if current.status is not PaymentIntentStatus.NEW:
            if status.state is PaymentState.PAID:
                current.checkout_state = "REVIEW"
                await self._payments.flush()
                raise PaymentSessionPendingError("A late payment requires financial review.")
            return current
        if status.amount != current.amount:
            raise PaymentSessionPendingError()
        if status.state is PaymentState.PAID:
            await self._settle(current)
        elif status.state is PaymentState.PENDING:
            if current.checkout_state == "CREATING" and status.payment_url:
                current.gateway_payment_url = status.payment_url
                current.checkout_state = "ACTIVE"
                await self._payments.flush()
        else:
            raise PaymentSessionPendingError()
        return current

    async def close(self, intent: PaymentIntent) -> PaymentIntent:
        """Keep ambiguous cancellation open so neither money nor order uniqueness is released."""
        current = await self.reconcile(intent)
        if current.status is not PaymentIntentStatus.NEW:
            return current
        if current.checkout_state == "CREATING":
            raise PaymentSessionPendingError()
        context = self.context(current)
        current.checkout_state = "CLOSING"
        await self._payments.checkpoint()
        await self._gateway.cancel_checkout(context)
        current = await self._lock(current)
        if current.status is PaymentIntentStatus.NEW:
            await self._reservations.release_locked_intent(current)
            current.status = PaymentIntentStatus.CANCELLED
            current.checkout_state = "CLOSED"
            await self._payments.flush()
        return current
