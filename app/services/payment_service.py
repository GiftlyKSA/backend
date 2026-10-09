"""Payment orchestration: top-ups, invoice payment, and the gateway webhook.

There are exactly two reasons to call the gateway — a wallet top-up and an invoice
remainder — both unified through ``payment_intents``. The webhook verifies
the HMAC over the raw body, looks up the simulated checkout, and dispatches on
``purpose``. Settlement is idempotent at three layers: a Redis lock on the transaction
payment-link ID, the intent's own status check, and the ledger's idempotency keys.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, Settings
from app.core.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    InvalidWebhookSignatureError,
    NotFoundError,
    PaymentAmountMismatchError,
    PaymentsDisabledError,
    PaymentSessionPendingError,
    ValidationDomainError,
)
from app.core.locks import redis_lock
from app.core.money import ZERO, parse_money, quantize_money
from app.integrations.payments.base import (
    PaymentCheckout,
    PaymentClient,
    PaymentContext,
    PaymentCustomer,
    PaymentItem,
    PaymentNotification,
    PaymentState,
    PaymentStatus,
)
from app.models import Invoice, InvoiceItem, Order, PaymentIntent, User
from app.models.enums import (
    InvoiceStatus,
    OrderStatus,
    PaymentIntentStatus,
    PaymentMethod,
    PaymentPurpose,
)
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.promo_repository import PromoRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.schemas.payments import DhamenWebhookAck
from app.services.hosted_payment_service import HostedPaymentService
from app.services.money_service import MoneyService
from app.services.order_state import assert_transition
from app.services.payment_context import invoice_snapshot
from app.services.payment_reservation_service import PaymentReservationService
from app.services.promo_service import PromoService

_WEBHOOK_LOCK_TTL = 15


@dataclass(frozen=True)
class TopupResult:
    """The outcome of starting a wallet top-up."""

    intent_id: uuid.UUID
    amount: Decimal
    payment_url: str | None
    status: str = "PENDING"
    session_reused: bool = False


@dataclass(frozen=True)
class PayResult:
    """The outcome of paying an invoice."""

    invoice_id: uuid.UUID
    status: str  # "PAID" (settled from wallet) or "PENDING" (awaiting the gateway)
    amount_from_wallet: Decimal
    amount_from_gateway: Decimal
    payment_url: str | None
    intent_id: uuid.UUID | None = None
    session_reused: bool = False
    use_wallet: bool = True


@dataclass(frozen=True)
class WebhookEvent:
    """A parsed gateway webhook payload."""

    payment_link_id: str
    status: str
    amount: Decimal | None


@dataclass(frozen=True)
class WebhookResult:
    """The outcome of processing a webhook."""

    outcome: str  # "processed" | "already_processed" | "failed"


class PaymentService:
    """Starts gateway payments and settles them on the webhook."""

    def __init__(
        self,
        *,
        payments: PaymentRepository,
        invoices: InvoiceRepository,
        orders: OrderRepository,
        wallets: WalletRepository,
        money: MoneyService,
        promos: PromoService,
        users: UserRepository,
        gateway: PaymentClient,
        redis: Redis,
        settings: Settings,
    ) -> None:
        """Wire the collaborators the payment flows need."""
        self._payments = payments
        self._invoices = invoices
        self._orders = orders
        self._wallets = wallets
        self._money = money
        self._promos = promos
        self._users = users
        self._gateway = gateway
        self._redis = redis
        self._settings = settings
        self._reservations = PaymentReservationService(
            payments=payments, wallets=wallets, money=money
        )
        self._hosted = HostedPaymentService(
            payments=payments,
            gateway=gateway,
            reservations=self._reservations,
            settle=self._settle_hosted_intent,
        )

    def _require_payments_available(self) -> None:
        self._require_recovery_available()
        if not self._settings.PAYMENT_CHECKOUTS_ENABLED:
            raise PaymentsDisabledError()

    def _require_recovery_available(self) -> None:
        if self._settings.is_production or self._settings.payment_provider == "disabled":
            raise PaymentsDisabledError()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _expiry(self) -> datetime:
        return self._now() + timedelta(hours=self._settings.PAYMENT_EXPIRY_HOURS)

    async def create_topup(
        self,
        *,
        user_id: uuid.UUID,
        amount: Decimal,
        on_intent: Callable[[uuid.UUID], Awaitable[None]] | None = None,
    ) -> TopupResult:
        """Start a wallet top-up: create the intent and a simulated payment link.

        Raises:
            ValidationDomainError: The amount is outside the permitted top-up bounds.
            NotFoundError: The user has no wallet.
        """
        self._require_payments_available()
        amount = quantize_money(amount)
        if not (self._settings.MIN_TOPUP_AMOUNT <= amount <= self._settings.MAX_TOPUP_AMOUNT):
            raise ValidationDomainError(
                f"Top-up must be between {self._settings.MIN_TOPUP_AMOUNT} and "
                f"{self._settings.MAX_TOPUP_AMOUNT}."
            )
        wallet = await self._wallets.get_by_user(user_id)
        if wallet is None:
            raise NotFoundError("Wallet not found.")

        if self._uses_hosted:
            reusable = await self._existing_topup(
                user_id=user_id, amount=amount, on_intent=on_intent
            )
            if reusable is not None:
                return reusable

        intent = await self._payments.create_intent(
            user_id=user_id,
            purpose=PaymentPurpose.WALLET_TOPUP,
            amount=amount,
            reference_invoice_id=None,
            expires_at=self._expiry(),
            use_wallet=False,
        )
        if on_intent is not None:
            await on_intent(intent.id)
        await self._payments.create_topup(
            user_id=user_id, wallet_id=wallet.id, payment_intent_id=intent.id, amount=amount
        )
        await self._money.stage_topup(user_wallet_id=wallet.id, amount=amount, intent_id=intent.id)
        if self._settings.ENVIRONMENT is Environment.DEVELOPMENT and not self._uses_hosted:
            await self._settle_topup(intent)
            await self._payments.mark_paid(intent, paid_at=self._now())
            return TopupResult(intent_id=intent.id, amount=amount, payment_url=None, status="PAID")

        checkout = await self._create_checkout(
            intent=intent,
            user_id=user_id,
            items=(
                PaymentItem(
                    name="Top up",
                    description=f"Top up for user {user_id}",
                    amount=amount,
                ),
            ),
        )
        if checkout is None:
            return TopupResult(intent_id=intent.id, amount=amount, payment_url=None, status="PAID")
        if not self._uses_hosted:
            await self._payments.attach_simulated_checkout(
                intent, payment_link_id=checkout.payment_link_id, url=checkout.payment_url
            )
        return TopupResult(intent_id=intent.id, amount=amount, payment_url=checkout.payment_url)

    async def _existing_topup(
        self,
        *,
        user_id: uuid.UUID,
        amount: Decimal,
        on_intent: Callable[[uuid.UUID], Awaitable[None]] | None = None,
    ) -> TopupResult | None:
        """Reuse a stable top-up and retain creation serialization after remote closure."""
        await self._payments.lock_topup_owner(user_id)
        existing = await self._payments.get_topup_for_actor(user_id, open_only=True)
        if existing is None:
            return None
        if existing.amount != amount:
            raise ConflictError("Cancel the existing top-up before changing its amount.")
        if on_intent is not None:
            await on_intent(existing.id)
        reusable = await self._hosted.reuse_or_close(existing)
        if reusable is not None:
            paid = reusable.status is PaymentIntentStatus.PAID
            return TopupResult(
                reusable.id,
                reusable.amount,
                None if paid else reusable.gateway_payment_url,
                "PAID" if paid else "PENDING",
                True,
            )
        await self._payments.lock_topup_owner(user_id)
        if await self._payments.get_topup_for_actor(user_id, open_only=True) is not None:
            raise PaymentSessionPendingError("Refresh the existing top-up session.")
        return None

    async def pay_invoice(
        self, *, invoice_id: uuid.UUID, customer_id: uuid.UUID, use_wallet: bool = True
    ) -> PayResult:
        """Pay an issued invoice from wallet, gateway, or a split of both.

        If the wallet fully covers the total, the payment settles synchronously into
        escrow and the order moves to IN_PROGRESS. Otherwise the wallet portion is held
        and a simulated payment link is created for the remainder; the webhook settles it.

        Raises:
            NotFoundError: No such invoice for this customer.
            ConflictError: The invoice is not payable (already paid/cancelled/expired).
            InvalidStateTransitionError: The order is not awaiting payment.
            InsufficientFundsError: A concurrent debit consumed the held balance.
        """
        self._require_payments_available()
        invoice = await self._invoices.get_for_actor(invoice_id, customer_id)
        if invoice is None:
            raise NotFoundError("Invoice not found.")
        invoice = await self._invoices.lock(invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found.")
        if invoice.status is not InvoiceStatus.ISSUED:
            raise ConflictError("This invoice is not awaiting payment.")
        if invoice.expires_at is not None and self._now() > invoice.expires_at:
            raise ConflictError("This invoice has expired.")

        order = await self._orders.lock(invoice.order_id)
        if order is None or order.customer_id != customer_id:
            raise NotFoundError("Order not found.")
        if order.status is not OrderStatus.WAITING_PAYMENT:
            raise InvalidStateTransitionError("This order is not awaiting payment.")

        existing = await self._existing_invoice_payment(invoice, order)
        if existing is not None:
            return self._reused_invoice_result(invoice, existing, use_wallet=use_wallet)

        wallet = await self._wallets.get_by_user(customer_id)
        if wallet is None:  # pragma: no cover - the customer always has a wallet
            raise NotFoundError("Wallet not found.")

        total = quantize_money(invoice.total_amount)
        available = await self._money.available_balance(customer_id) if use_wallet else ZERO
        wallet_amount = min(available, total)
        gateway_amount = quantize_money(total - wallet_amount)

        if gateway_amount <= ZERO:
            # Wallet fully covers the total — settle immediately, no gateway round-trip.
            await self._money.fund_escrow_for_invoice(
                customer_wallet_id=wallet.id,
                wallet_amount=total,
                gateway_amount=ZERO,
                invoice_id=invoice.id,
                order_id=order.id,
                intent_id=None,
                was_held=False,
            )
            await self._settle_invoice_record(
                invoice, order, PaymentMethod.WALLET_ONLY, total, ZERO
            )
            return PayResult(
                invoice_id=invoice.id,
                status="PAID",
                amount_from_wallet=total,
                amount_from_gateway=ZERO,
                payment_url=None,
                use_wallet=use_wallet,
            )

        return await self._start_invoice_gateway_payment(
            invoice=invoice,
            wallet_id=wallet.id,
            customer_id=customer_id,
            wallet_amount=wallet_amount,
            gateway_amount=gateway_amount,
            use_wallet=use_wallet,
        )

    def _reused_invoice_result(
        self, invoice: Invoice, intent: PaymentIntent, *, use_wallet: bool
    ) -> PayResult:
        """Return the frozen split; never change funding behind an active hosted URL."""
        frozen_wallet = (
            bool(intent.checkout_snapshot.get("use_wallet", True))
            if self._uses_hosted and intent.checkout_snapshot
            else bool(getattr(intent, "use_wallet", True))
        )
        paid = intent.status is PaymentIntentStatus.PAID
        if not paid and frozen_wallet != use_wallet:
            raise ConflictError("Cancel the existing session before changing wallet use.")
        return PayResult(
            invoice_id=invoice.id,
            status="PAID" if paid else "PENDING",
            amount_from_wallet=intent.wallet_reserved_amount,
            amount_from_gateway=intent.amount,
            payment_url=None if paid else intent.gateway_payment_url,
            intent_id=intent.id,
            session_reused=True,
            use_wallet=frozen_wallet,
        )

    async def _existing_invoice_payment(
        self, invoice: Invoice, order: Order
    ) -> PaymentIntent | None:
        """Reuse one order session or close it before replacing the payment attempt."""
        if not self._uses_hosted:
            return await self._payments.get_open_intent_for_invoice(invoice.id)
        existing = await self._payments.get_open_intent_for_order(order.id)
        if existing is None:
            return None
        if existing.reference_invoice_id != invoice.id:
            closed = await self._hosted.close(existing)
            if closed.status is PaymentIntentStatus.PAID:
                raise PaymentSessionPendingError("A previous invoice payment requires review.")
            existing = None
        else:
            existing = await self._hosted.reuse_or_close(existing)
        if existing is None:
            current_invoice = await self._invoices.lock(invoice.id)
            current_order = await self._orders.lock(order.id)
            if (
                current_invoice is None
                or current_invoice.status is not InvoiceStatus.ISSUED
                or current_order is None
                or current_order.status is not OrderStatus.WAITING_PAYMENT
                or current_invoice.expires_at is None
                or current_invoice.expires_at <= self._now()
            ):
                raise ConflictError("Refresh the current unpaid invoice.")
            winner = await self._payments.get_open_intent_for_order(order.id)
            if winner is not None:
                if winner.reference_invoice_id != invoice.id:
                    raise PaymentSessionPendingError("Another invoice payment is in progress.")
                return await self._hosted.reuse_or_close(winner)
        return existing

    async def _start_invoice_gateway_payment(
        self,
        *,
        invoice: Invoice,
        wallet_id: uuid.UUID,
        customer_id: uuid.UUID,
        wallet_amount: Decimal,
        gateway_amount: Decimal,
        use_wallet: bool = True,
    ) -> PayResult:
        """Create and settle-or-send the invoice remainder payment."""
        method = PaymentMethod.SPLIT if wallet_amount > ZERO else PaymentMethod.GATEWAY_ONLY
        if self._uses_hosted and gateway_amount < Decimal("1.00"):
            raise ValidationDomainError("The Dhamen remainder must be at least 1.00 SAR.")
        invoice.amount_from_wallet = wallet_amount
        invoice.amount_from_gateway = gateway_amount
        invoice.payment_method = method

        intent = await self._payments.create_intent(
            user_id=customer_id,
            purpose=PaymentPurpose.ORDER_INVOICE,
            amount=gateway_amount,
            reference_invoice_id=invoice.id,
            expires_at=min(self._expiry(), invoice.expires_at)
            if invoice.expires_at
            else self._expiry(),
            wallet_reserved_amount=wallet_amount,
            use_wallet=use_wallet,
        )
        await self._money.stage_invoice_payment(
            customer_wallet_id=wallet_id,
            wallet_amount=wallet_amount,
            gateway_amount=gateway_amount,
            invoice_id=invoice.id,
            order_id=invoice.order_id,
            intent_id=intent.id,
        )
        if self._settings.ENVIRONMENT is Environment.DEVELOPMENT and not self._uses_hosted:
            await self._settle_invoice(intent)
            await self._payments.mark_paid(intent, paid_at=self._now())
            return PayResult(
                invoice_id=invoice.id,
                status="PAID",
                amount_from_wallet=wallet_amount,
                amount_from_gateway=gateway_amount,
                payment_url=None,
                use_wallet=use_wallet,
            )

        checkout = await self._create_checkout(
            intent=intent,
            user_id=customer_id,
            items=self._invoice_checkout_items(
                invoice_id=invoice.id,
                payment_amount=gateway_amount,
                invoice_items=await self._invoices.list_items(invoice.id),
            ),
            use_wallet=use_wallet,
        )
        if checkout is None:
            return PayResult(
                invoice_id=invoice.id,
                status="PAID",
                amount_from_wallet=wallet_amount,
                amount_from_gateway=gateway_amount,
                payment_url=None,
                intent_id=intent.id,
                use_wallet=use_wallet,
            )
        if not self._uses_hosted:
            await self._payments.attach_simulated_checkout(
                intent, payment_link_id=checkout.payment_link_id, url=checkout.payment_url
            )
        await self._invoices.flush()
        return PayResult(
            invoice_id=invoice.id,
            status="PENDING",
            amount_from_wallet=wallet_amount,
            amount_from_gateway=gateway_amount,
            payment_url=checkout.payment_url,
            intent_id=intent.id,
            use_wallet=use_wallet,
        )

    @property
    def _uses_hosted(self) -> bool:
        return self._gateway.uses_hosted_sessions is True

    async def _settle_hosted_intent(self, intent: PaymentIntent) -> None:
        """Settle only the originally bound invoice and reject stale lifecycle successes."""
        if intent.status is not PaymentIntentStatus.NEW:
            return
        if intent.purpose is PaymentPurpose.WALLET_TOPUP:
            await self._settle_topup(intent)
        else:
            if intent.reference_invoice_id is None:
                raise PaymentSessionPendingError("The payment has no invoice reference.")
            invoice = await self._invoices.lock(intent.reference_invoice_id)
            if invoice is None or invoice.status is not InvoiceStatus.ISSUED:
                intent.checkout_state = "REVIEW"
                await self._payments.flush()
                raise PaymentSessionPendingError(
                    "This payment requires reconciliation with its invoice."
                )
            await self._settle_invoice(intent)
        intent.checkout_state = "CLOSED"
        await self._payments.mark_paid(intent, paid_at=self._now())

    async def get_payment_session(
        self, *, intent_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentIntent:
        """Read an owned session without making a provider request."""
        intent = await self._payments.get_intent_for_actor(intent_id, user_id)
        if intent is None:
            raise NotFoundError("Payment session not found.")
        return intent

    async def get_order_payment_session(
        self, *, order_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentIntent:
        """Recover a session without creating a new attempt after a lost checkout response."""
        intent = await self._payments.get_latest_intent_for_order(
            order_id=order_id, user_id=user_id
        )
        if intent is None:
            raise NotFoundError("Payment session not found.")
        return intent

    async def refresh_payment_session(
        self, *, intent_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentIntent:
        """Reconcile an owned real checkout with its authoritative provider state."""
        self._require_recovery_available()
        intent = await self.get_payment_session(intent_id=intent_id, user_id=user_id)
        if self._uses_hosted:
            return await self._hosted.reconcile(intent)
        return intent

    async def cancel_payment_session(
        self, *, intent_id: uuid.UUID, user_id: uuid.UUID
    ) -> PaymentIntent:
        """Close an owned checkout and release its reservation after confirmed closure."""
        self._require_recovery_available()
        intent = await self.get_payment_session(intent_id=intent_id, user_id=user_id)
        if not self._uses_hosted:
            raise ConflictError("Simulation sessions use the simulation callback.")
        return await self._hosted.close(intent)

    async def get_topup_payment_session(self, *, user_id: uuid.UUID) -> PaymentIntent:
        """Recover the latest owned hosted top-up after a lost create response."""
        intent = await self._payments.get_topup_for_actor(user_id)
        if intent is None:
            raise NotFoundError("Top-up session not found.")
        return intent

    async def close_invoice_payment(self, *, invoice_id: uuid.UUID) -> None:
        """Close a previously authorized courier invoice's payment before cancellation."""
        invoice = await self._invoices.lock(invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found.")
        await self._orders.lock(invoice.order_id)
        intent = await self._payments.get_open_intent_for_order(invoice.order_id)
        if intent is None:
            return
        if intent.checkout_state == "REVIEW":
            raise PaymentSessionPendingError("This invoice payment requires review.")
        if intent.checkout_provider == "SIMULATED":
            await self._reservations.expire_for_invoice(invoice_id)
            return
        self._require_recovery_available()
        closed = await self._hosted.close(intent)
        if closed.status is PaymentIntentStatus.PAID:
            await self._payments.checkpoint()
            raise ConflictError("Payment completed before cancellation; refresh the invoice.")

    async def handle_webhook(self, *, raw_body: bytes, signature: str) -> WebhookResult:
        """Verify and process a gateway webhook.

        The signature is verified over the RAW body (never a re-serialized dict). A Redis
        lock on the simulated checkout ID serializes concurrent duplicate deliveries.

        Raises:
            InvalidWebhookSignatureError: The HMAC does not match.
            NotFoundError: No intent matches the simulated checkout ID.
            PaymentAmountMismatchError: The webhook amount != the intent amount.
        """
        self._require_payments_available()
        if not self._gateway.verify_webhook_signature(raw_body, signature):
            raise InvalidWebhookSignatureError()
        event = self._parse(raw_body)
        async with redis_lock(
            self._redis, f"lock:webhook:{event.payment_link_id}", ttl_seconds=_WEBHOOK_LOCK_TTL
        ):
            return await self._settle_locked(event)

    async def handle_dhamen_notifications(self, *, raw_body: bytes) -> DhamenWebhookAck:
        """Treat testing callbacks as hints; only authenticated provider status can settle."""
        self._require_recovery_available()
        if self._gateway.provider != "DHAMEN":
            raise PaymentsDisabledError()
        notifications = self._gateway.parse_notifications(raw_body)
        verified = await self._verify_dhamen_payments(notifications)
        await self._payments.resume_actor()
        locked = await self._payments.lock_session_batch([intent.id for intent, _ in verified])
        await self._quarantine_late_dhamen_payments(verified, locked)
        await self._wallets.lock_payment_batch(
            intent_ids=[intent.id for intent, _ in verified],
            user_ids=[intent.user_id for intent in locked.values()],
        )
        for intent, status in verified:
            await self._hosted.apply_locked_status(locked[intent.id], status)
        raw_hash = hashlib.sha256(raw_body).hexdigest()
        for notification in notifications:
            await self._payments.resume_actor()
            await self._payments.insert_notification_receipt_if_new(
                notification_id=notification.notification_id,
                batch_id=notification.batch_id,
                notification_type=notification.notification_type,
                payment_reference=notification.references[0]
                if len(notification.references) == 1
                else None,
                transaction_id=None,
                raw_hash=raw_hash,
                processing_outcome="RECONCILED" if notification.should_reconcile else "IGNORED",
            )
        return DhamenWebhookAck(response_id=str(uuid.uuid4()))

    async def _quarantine_late_dhamen_payments(
        self,
        verified: list[tuple[PaymentIntent, PaymentStatus | None]],
        locked: dict[uuid.UUID, PaymentIntent],
    ) -> None:
        """Persist anomalous late payment markers before any batch money movement."""
        review_required = False
        for intent, status in verified:
            current = locked.get(intent.id)
            if current is None:
                raise NotFoundError("Payment session not found.")
            if (
                current.status not in {PaymentIntentStatus.NEW, PaymentIntentStatus.PAID}
                and status is not None
                and status.state is PaymentState.PAID
            ):
                current.checkout_state = "REVIEW"
                review_required = True
        if review_required:
            await self._payments.checkpoint()
            raise PaymentSessionPendingError("A late payment requires financial review.")

    async def _verify_dhamen_payments(
        self, notifications: tuple[PaymentNotification, ...]
    ) -> list[tuple[PaymentIntent, PaymentStatus | None]]:
        """Verify each distinct reference before the batch settlement transaction starts."""
        references = sorted(
            {
                reference
                for notification in notifications
                if notification.should_reconcile
                for reference in notification.references
            }
        )
        verified = []
        for reference in references:
            try:
                intent_id = uuid.UUID(reference)
            except ValueError as exc:
                raise ValidationDomainError("Unknown Dhamen payment reference.") from exc
            intent = await self._payments.get_intent(intent_id)
            if (
                intent is None
                or intent.checkout_provider != "DHAMEN"
                or intent.gateway_reference != reference
            ):
                raise NotFoundError("Unknown Dhamen payment reference.")
            status = await self._hosted.verify_status(intent)
            verified.append((intent, status))
        return verified

    async def _settle_locked(self, event: WebhookEvent) -> WebhookResult:
        candidate = await self._payments.get_intent_by_payment_link(event.payment_link_id)
        if candidate is None:
            raise NotFoundError("Unknown simulated payment link.")
        invoice_id = candidate.reference_invoice_id
        if invoice_id is not None:
            # Invoice-first locking matches retries and expiry; refresh after waiting.
            if await self._invoices.lock(invoice_id) is None:
                raise NotFoundError("Invoice not found.")
        intent = await self._payments.lock_intent_by_payment_link(event.payment_link_id)
        if intent is None:
            raise NotFoundError("Unknown simulated payment link.")
        if intent.reference_invoice_id != invoice_id:
            raise ConflictError("The payment intent invoice reference changed.")
        if intent.status is not PaymentIntentStatus.NEW:
            # Already PAID/FAILED — a replay. Idempotent no-op.
            return WebhookResult(outcome="already_processed")
        if event.status.upper() != "PAID":
            await self._reservations.release_locked_intent(intent)
            await self._payments.mark_failed(intent, reason=event.status)
            return WebhookResult(outcome="failed")
        if event.amount is None or quantize_money(event.amount) != quantize_money(intent.amount):
            raise PaymentAmountMismatchError()

        if intent.purpose is PaymentPurpose.WALLET_TOPUP:
            await self._settle_topup(intent)
        else:
            await self._settle_invoice(intent)
        await self._payments.mark_paid(intent, paid_at=self._now())
        return WebhookResult(outcome="processed")

    async def _settle_topup(self, intent: PaymentIntent) -> None:
        wallet = await self._wallets.get_by_user(intent.user_id)
        if wallet is None:  # pragma: no cover - the top-up user always has a wallet
            raise NotFoundError("Wallet not found.")
        await self._money.credit_topup(
            user_wallet_id=wallet.id, amount=intent.amount, intent_id=intent.id
        )

    async def _settle_invoice(self, intent: PaymentIntent) -> None:
        if intent.reference_invoice_id is None:
            raise ConflictError("The payment intent has no invoice reference.")
        invoice = await self._invoices.lock(intent.reference_invoice_id)
        if invoice is None:  # pragma: no cover - FK guarantees the invoice exists
            raise NotFoundError("Invoice not found.")
        if invoice.status is InvoiceStatus.PAID:  # pragma: no cover - intent guard precedes
            return
        if invoice.status is not InvoiceStatus.ISSUED:
            raise ConflictError("This invoice is not awaiting payment.")
        order = await self._orders.lock(invoice.order_id)
        if order is None:  # pragma: no cover - FK guarantees the order exists
            raise NotFoundError("Order not found.")
        wallet = await self._wallets.get_by_user(intent.user_id)
        if wallet is None:  # pragma: no cover
            raise NotFoundError("Wallet not found.")

        await self._money.fund_escrow_for_invoice(
            customer_wallet_id=wallet.id,
            wallet_amount=intent.wallet_reserved_amount,
            gateway_amount=intent.amount,
            invoice_id=invoice.id,
            order_id=order.id,
            intent_id=intent.id,
            was_held=intent.wallet_reserved_amount > ZERO,
        )
        method = invoice.payment_method or PaymentMethod.GATEWAY_ONLY
        await self._settle_invoice_record(
            invoice, order, method, intent.wallet_reserved_amount, intent.amount
        )

    async def _settle_invoice_record(
        self,
        invoice: Invoice,
        order: Order,
        method: PaymentMethod,
        wallet_amount: Decimal,
        gateway_amount: Decimal,
    ) -> None:
        """Mark the invoice PAID, advance the order, and consume the promo."""
        now = self._now()
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = now
        invoice.payment_method = method
        invoice.amount_from_wallet = quantize_money(wallet_amount)
        invoice.amount_from_gateway = quantize_money(gateway_amount)
        assert_transition(order.status, OrderStatus.IN_PROGRESS)
        order.status = OrderStatus.IN_PROGRESS
        await self._promos.consume(invoice_id=invoice.id)
        await self._invoices.flush()

    async def _create_checkout(
        self,
        *,
        intent: PaymentIntent,
        user_id: uuid.UUID,
        items: tuple[PaymentItem, ...],
        use_wallet: bool = True,
    ) -> PaymentCheckout | None:
        """Create a single-use hosted checkout for a known local customer."""
        user = await self._users.get(user_id)
        if user is None:  # pragma: no cover - intent FK guarantees the user exists
            raise NotFoundError("User not found.")
        if self._uses_hosted:
            invoice = (
                await self._invoices.lock(intent.reference_invoice_id)
                if intent.reference_invoice_id is not None
                else None
            )
            context = PaymentContext(
                reference=str(intent.id),
                customer=self._checkout_customer(user),
                amount=intent.amount,
                currency=intent.currency,
                expires_at=intent.expires_at,
                amount_from_wallet=intent.wallet_reserved_amount,
                use_wallet=use_wallet if invoice is not None else False,
                title="Invoice payment" if invoice is not None else "Top up",
                description=f"Invoice {invoice.id}"
                if invoice is not None
                else f"Top up for {user.full_name or 'Giftly customer'} ({user.id})",
                invoice=invoice_snapshot(invoice, await self._invoices.list_items(invoice.id))
                if invoice is not None
                else None,
            )
            current = await self._hosted.create(intent, context)
            if current.status is PaymentIntentStatus.PAID:
                return None
            if (
                current.status is not PaymentIntentStatus.NEW
                or current.gateway_payment_url is None
                or current.checkout_state != "ACTIVE"
                or current.expires_at <= self._now()
            ):
                raise PaymentSessionPendingError()
            return PaymentCheckout(str(current.id), current.gateway_payment_url)
        return await self._gateway.create_payment_link(
            reference=str(intent.id),
            customer=self._checkout_customer(user),
            items=items,
            success_redirect_url=None,
            failure_redirect_url=None,
        )

    @staticmethod
    def _checkout_customer(user: User) -> PaymentCustomer:
        """Map the minimum local customer identity simulated payment needs for hosted checkout."""
        return PaymentCustomer(
            external_id=str(user.id),
            name=user.full_name or "Giftly customer",
            phone_number=user.phone,
            email=user.email,
        )

    @staticmethod
    def _invoice_checkout_items(
        *, invoice_id: uuid.UUID, payment_amount: Decimal, invoice_items: list[InvoiceItem]
    ) -> tuple[PaymentItem, ...]:
        """Represent a payable invoice as simulated checkout items whose sum equals the remainder.

        When the full invoice is paid externally, frozen invoice lines are sent one-for-one
        plus a visible invoice adjustment for delivery, fees, and discounts. A split
        payment may be smaller than the item total, so it uses one authoritative balance
        line to ensure the simulated invoice matches the amount held in our ledger exactly.
        """
        source_total = sum((quantize_money(item.line_total_amount) for item in invoice_items), ZERO)
        if (
            not invoice_items
            or source_total > payment_amount
            or any(item.line_total_amount < Decimal("1.00") for item in invoice_items)
        ):
            return (
                PaymentItem(
                    name=f"Giftly invoice {invoice_id}",
                    description="Outstanding invoice balance",
                    amount=payment_amount,
                ),
            )

        items = [
            PaymentItem(
                name=item.title,
                description=(f"Quantity: {item.quantity}. {item.description or ''}".strip()),
                amount=quantize_money(item.line_total_amount),
            )
            for item in invoice_items
        ]
        adjustment = quantize_money(payment_amount - source_total)
        if adjustment >= Decimal("1.00"):
            items.append(
                PaymentItem(
                    name="Invoice adjustment",
                    description="Delivery, service fees, and discounts",
                    amount=adjustment,
                )
            )
        elif adjustment > ZERO:
            last = items[-1]
            items[-1] = PaymentItem(
                name=last.name,
                description=last.description,
                amount=quantize_money(last.amount + adjustment),
            )
        return tuple(items)

    @staticmethod
    def _parse(raw_body: bytes) -> WebhookEvent:
        try:
            data = json.loads(raw_body)
            details = data.get("data", {})
            if not isinstance(details, dict):
                raise TypeError("data must be an object")
            payment_link = details.get("payment_link", {})
            payment = details.get("payment", {})
            invoice = details.get("invoice", {})
            if not isinstance(payment_link, dict) or not isinstance(payment, dict):
                raise TypeError("payment data must be an object")
            amount = payment.get(
                "amount", invoice.get("amount") if isinstance(invoice, dict) else None
            )
            event_type = str(data.get("event_type", ""))
            return WebhookEvent(
                payment_link_id=str(payment_link["id"]),
                status=(
                    "PAID" if event_type.upper() == "PAYMENT_SUCCEEDED" else str(payment["status"])
                ),
                amount=parse_money(str(amount)) if amount is not None else None,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValidationDomainError("Malformed webhook payload.") from exc


def build_payment_service(
    *, session: AsyncSession, gateway: PaymentClient, redis: Redis, settings: Settings
) -> PaymentService:
    """Assemble a PaymentService with fresh repositories bound to one session."""
    return PaymentService(
        payments=PaymentRepository(session),
        invoices=InvoiceRepository(session),
        orders=OrderRepository(session),
        wallets=WalletRepository(session),
        users=UserRepository(session),
        money=MoneyService(WalletRepository(session)),
        promos=PromoService(PromoRepository(session)),
        gateway=gateway,
        redis=redis,
        settings=settings,
    )
