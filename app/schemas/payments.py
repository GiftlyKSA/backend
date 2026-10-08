"""Pydantic contracts for the payment endpoints.

Money crosses the wire as decimal strings, never floats. Amounts the client sends
(a top-up value) are parsed to Decimal; the gateway webhook payload is validated the
same way before the raw-body signature is trusted.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from app.core.money import MoneyError, parse_money
from app.schemas.invoices import InvoiceResponse

_MoneyStr = Annotated[str, StringConstraints(min_length=1, max_length=20)]
_Txn = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class TopupRequest(BaseModel):
    """Start a wallet top-up for a bounded amount."""

    model_config = ConfigDict(extra="forbid")
    amount: _MoneyStr = Field(..., description='Top-up amount, e.g. "500.00".')

    @field_validator("amount")
    @classmethod
    def _valid_amount(cls, value: str) -> str:
        try:
            if parse_money(value) <= Decimal(0):
                raise ValueError("amount must be positive.")
        except MoneyError as exc:
            raise ValueError(str(exc)) from exc
        return value


class TopupResponse(BaseModel):
    """A wallet top-up; ``payment_url`` is null when development settles it directly."""

    payment_intent_id: str
    amount: str
    payment_url: str | None = None
    status: str = "PENDING"
    session_reused: bool = False


class PayInvoiceRequest(BaseModel):
    """Choose wallet funding; the backend determines all payment amounts."""

    model_config = ConfigDict(extra="forbid")
    use_wallet: StrictBool = True


class PayInvoiceResponse(BaseModel):
    """The result of paying an invoice.

    ``status`` is ``PAID`` when the wallet covered the total or development settles the
    gateway portion directly. ``PENDING`` requires a gateway payment and has ``payment_url``.
    """

    invoice_id: str
    status: str
    amount_from_wallet: str
    amount_from_gateway: str
    payment_url: str | None = None
    payment_intent_id: str | None = None
    session_reused: bool = False
    use_wallet: bool = True


class PaymentSessionResponse(BaseModel):
    """Owned checkout status and the immutable invoice used to create it."""

    payment_intent_id: str
    provider: str
    status: str
    checkout_state: str
    order_id: str | None
    invoice_id: str | None
    currency: str
    amount_from_wallet: str
    amount_from_gateway: str
    expires_at: str
    payment_url: str | None
    invoice: InvoiceResponse | None
    purpose: str = "ORDER_INVOICE"
    title: str = "Invoice payment"
    description: str | None = None
    use_wallet: bool = True


class WebhookAck(BaseModel):
    """The webhook acknowledgement returned to the gateway."""

    outcome: str


class DhamenWebhookAck(BaseModel):
    """The acknowledgement required by Dhamen after durable acceptance."""

    model_config = ConfigDict(populate_by_name=True)
    response_id: str = Field(alias="responseId")
    status: str = "SUCCESS"


class SimulatePaymentRequest(BaseModel):
    """Development-only: simulate a simulated payment callback for a payment-link ID."""

    model_config = ConfigDict(extra="forbid")
    payment_link_id: _Txn
    status: Annotated[str, StringConstraints(min_length=1, max_length=32)] = "PAID"
