"""Provider-neutral single-use checkout contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.core.exceptions import PaymentsDisabledError, ValidationDomainError
from app.schemas.invoices import InvoiceResponse


@dataclass(frozen=True)
class PaymentCustomer:
    """The known payer attached to a simulated payment checkout."""

    external_id: str
    name: str
    phone_number: str
    email: str | None


@dataclass(frozen=True)
class PaymentItem:
    """One immutable invoice item represented as a simulated payment one-time product."""

    name: str
    description: str | None
    amount: Decimal


@dataclass(frozen=True)
class PaymentCheckout:
    """The hosted checkout URL and its provider payment-link ID."""

    payment_link_id: str
    payment_url: str


class PaymentState(StrEnum):
    """Authoritative provider states, independent of transport-specific codes."""

    PENDING = "PENDING"
    PAID = "PAID"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class PaymentContext(BaseModel):
    """Frozen invoice details and the exact amount charged by one checkout."""

    model_config = ConfigDict(frozen=True)

    reference: str
    customer: PaymentCustomer
    amount: Decimal
    currency: str
    expires_at: datetime
    invoice: InvoiceResponse | None = None
    amount_from_wallet: Decimal = Decimal("0.00")
    use_wallet: bool = True
    title: str = "Invoice payment"
    description: str | None = None


@dataclass(frozen=True)
class PaymentStatus:
    """A status bound to a known payment reference and customer."""

    state: PaymentState
    amount: Decimal
    payment_url: str | None = None


@dataclass(frozen=True)
class PaymentNotification:
    """Provider notification interpreted as a bounded reconciliation hint."""

    batch_id: str
    notification_id: str
    notification_type: str
    references: tuple[str, ...]
    should_reconcile: bool


class PaymentClient(ABC):
    """Replaceable gateway composed by the application payment services."""

    provider = "SIMULATED"
    uses_hosted_sessions = False

    def validate_checkout(self, context: PaymentContext) -> None:
        """Validate known provider limits before committing a creation claim."""
        if (
            not context.amount.is_finite()
            or context.amount <= 0
            or context.expires_at.tzinfo is None
        ):
            raise ValidationDomainError("Invalid checkout amount or expiry.")

    def parse_notifications(self, raw_body: bytes) -> tuple[PaymentNotification, ...]:
        """Interpret provider transport data without authorizing financial changes."""
        raise PaymentsDisabledError()

    async def create_checkout(self, context: PaymentContext) -> PaymentCheckout:
        """Create a checkout from immutable financial context."""
        raise PaymentsDisabledError()

    async def get_payment_status(self, context: PaymentContext) -> PaymentStatus:
        """Read authoritative status without interpreting redirects as payment."""
        raise PaymentsDisabledError()

    async def cancel_checkout(self, context: PaymentContext) -> None:
        """Confirm closure before a checkout may be replaced."""
        raise PaymentsDisabledError()

    @abstractmethod
    async def create_payment_link(
        self,
        *,
        reference: str,
        customer: PaymentCustomer,
        items: tuple[PaymentItem, ...],
        success_redirect_url: str | None,
        failure_redirect_url: str | None,
    ) -> PaymentCheckout:
        """Create a one-time simulated payment link for the supplied invoice items."""

    @abstractmethod
    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify simulated payment's timestamped HMAC signature in constant time."""
