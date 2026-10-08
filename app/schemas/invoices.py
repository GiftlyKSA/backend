"""Pydantic contracts for the invoice and promo-preview endpoints.

Money crosses the wire as decimal strings, never floats: a float is the single mistake
that reintroduces binary rounding error. Amounts the client sends (unit price, courier
fee) are parsed to Decimal; everything else on an invoice is server-computed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.money import MoneyError, parse_money
from app.core.promo_codes import normalize_code

_Title = Annotated[str, StringConstraints(min_length=1, max_length=120)]
_Description = Annotated[str, StringConstraints(max_length=500)]
_Code = Annotated[str, StringConstraints(min_length=1, max_length=32)]
_MoneyStr = Annotated[str, StringConstraints(min_length=1, max_length=20)]


class InvoiceLineRequest(BaseModel):
    """One courier-authored invoice line with its final unit price."""

    model_config = ConfigDict(extra="forbid")
    title: _Title
    description: _Description | None = None
    unit_price_amount: _MoneyStr = Field(..., description='Final unit price, e.g. "400.00".')
    quantity: int = Field(..., ge=1, le=999)

    @field_validator("unit_price_amount")
    @classmethod
    def _valid_price(cls, value: str) -> str:
        try:
            parse_money(value)
        except MoneyError as exc:
            raise ValueError(str(exc)) from exc
        return value


class CreateInvoiceRequest(BaseModel):
    """Author and issue an invoice for an order."""

    model_config = ConfigDict(extra="forbid")
    items: list[InvoiceLineRequest] = Field(..., min_length=1, max_length=20)
    courier_fee_amount: _MoneyStr = Field("0.00", description="Courier's craft/labour fee.")
    promo_code: _Code | None = None

    @field_validator("promo_code")
    @classmethod
    def _valid_code(cls, value: str | None) -> str | None:
        if value is not None:
            value = normalize_code(value)
            if not 1 <= len(value) <= 32:
                raise ValueError("Enter a promo code between 1 and 32 characters.")
        return value

    @field_validator("courier_fee_amount")
    @classmethod
    def _valid_fee(cls, value: str) -> str:
        try:
            if parse_money(value) < Decimal(0):
                raise ValueError("courier_fee_amount must not be negative.")
        except MoneyError as exc:
            raise ValueError(str(exc)) from exc
        return value


class PromoValidateRequest(BaseModel):
    """Preview a promo against an order's active invoice."""

    model_config = ConfigDict(extra="forbid")
    code: _Code
    order_id: UUID = Field(..., description="The order whose active invoice to price against.")

    @field_validator("code")
    @classmethod
    def _valid_code(cls, value: str) -> str:
        normalized = normalize_code(value)
        if not 1 <= len(normalized) <= 32:
            raise ValueError("Enter a promo code between 1 and 32 characters.")
        return normalized


class ApplyInvoicePromoRequest(BaseModel):
    """Apply a case-insensitive code, or remove it with explicit null."""

    model_config = ConfigDict(extra="forbid")
    code: _Code | None = Field(...)

    @field_validator("code")
    @classmethod
    def _valid_code(cls, value: str | None) -> str | None:
        return CreateInvoiceRequest._valid_code(value)


class InvoiceItemResponse(BaseModel):
    """A single computed invoice line, as stored."""

    position: int
    title: str
    description: str | None
    unit_price_amount: str
    quantity: int
    line_net_amount: str
    line_discount_amount: str
    line_total_amount: str


class InvoiceResponse(BaseModel):
    """A full invoice view for its participants (every amount server-computed)."""

    id: str
    order_id: str
    status: str
    currency: str
    items_net_amount: str
    courier_fee_amount: str
    service_fee_amount: str
    discount_amount: str
    net_after_discount_amount: str
    total_amount: str
    promo_code: str | None
    issued_at: str | None
    expires_at: str | None
    items: list[InvoiceItemResponse]


class PromoPreviewResponse(BaseModel):
    """A previewed promo discount and the total it would produce."""

    code: str
    discount_amount: str
    original_total_amount: str
    total_amount: str
