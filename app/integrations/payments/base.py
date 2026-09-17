"""Local payment simulation contract used by payment orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


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


class PaymentClient(ABC):
    """Creates hosted simulated payment checkouts and authenticates local simulation callbacks."""

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
