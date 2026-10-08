"""Dhamen v1.5 hosted checkout; callback bodies never establish payment truth."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.exceptions import PaymentProviderUnavailableError, ValidationDomainError
from app.core.money import money_str
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
from app.integrations.payments.dhamen_notifications import parse_dhamen_notifications


class _CheckoutRow(BaseModel):
    customer_identifier: str = Field(alias="customerIdentifier")
    payment_url: str = Field(alias="paymentUrl")
    invoice_id: str = Field(alias="invoiceId")


class _StatusRow(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    payment_reference_id: str = Field(alias="paymentReferenceId")
    customer_identifier: str = Field(alias="customerIdentifier")
    amount: Decimal = Field(ge=0, le=50000, decimal_places=2, max_digits=12)
    payment_status: Literal[0, 1] = Field(alias="paymentStatus")
    payment_url: str | None = Field(default=None, alias="paymentUrl")

    @field_validator("payment_status", mode="before")
    @classmethod
    def validate_status_integer(cls, value: object) -> object:
        """Reject booleans and decimal values before interpreting financial status."""
        if type(value) is not int:
            raise ValueError("Payment status must be an integer.")
        return value


class _CheckoutResponse(BaseModel):
    customer_payments: list[_CheckoutRow] = Field(
        alias="customerPayments", min_length=1, max_length=1
    )


class _StatusResponse(BaseModel):
    customer_payments: list[_StatusRow] = Field(
        alias="customerPayments", min_length=1, max_length=1
    )


class DhamenPaymentClient(PaymentClient):
    """Translate provider-neutral operations to the documented Dhamen endpoints."""

    provider = "DHAMEN"
    uses_hosted_sessions = True

    @staticmethod
    def parse_notifications(raw_body: bytes) -> tuple[PaymentNotification, ...]:
        """Normalize callback hints; authenticated status lookup determines settlement."""
        return parse_dhamen_notifications(raw_body)

    def __init__(
        self,
        *,
        base_url: str,
        app_id: str,
        app_key: str,
        client_id: str,
        checkout_hosts: tuple[str, ...],
        return_url: str,
        timeout_seconds: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a bounded shared client; credentials never enter payloads or logs."""
        self._checkout_hosts = checkout_hosts
        self._return_url = return_url
        self._timeout_seconds = timeout_seconds
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={
                "App-id": app_id,
                "App-key": app_key,
                "ClientId": client_id,
                "api-version": "2",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release the shared HTTP pool on application shutdown."""
        await self._http.aclose()

    async def _request(self, path: str, body: str, *, method: str = "POST") -> object:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._bounded_request(path, body, method=method)
        except TimeoutError as exc:
            raise PaymentProviderUnavailableError() from exc

    async def _bounded_request(self, path: str, body: str, *, method: str) -> object:
        try:
            async with self._http.stream(method, path, content=body.encode()) as response:
                if response.status_code != 200:
                    raise PaymentProviderUnavailableError()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 65536:
                        raise PaymentProviderUnavailableError()
                return json.loads(chunks, parse_float=Decimal)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise PaymentProviderUnavailableError() from exc

    def _safe_url(self, url: str) -> str:
        parsed = urlsplit(url)
        if (
            len(url) > 512
            or parsed.scheme != "https"
            or parsed.hostname not in self._checkout_hosts
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise PaymentProviderUnavailableError()
        return url

    def validate_checkout(self, context: PaymentContext) -> None:
        """Reject locally known invalid inputs before persisting a remote creation claim."""
        if (
            context.currency != "SAR"
            or not context.amount.is_finite()
            or context.amount < Decimal("1.00")
            or context.amount > Decimal("50000.00")
            or len(context.reference) > 50
            or context.expires_at.tzinfo is None
        ):
            raise ValidationDomainError("This amount or reference cannot use Dhamen checkout.")
        if context.expires_at <= datetime.now(UTC):
            raise ValidationDomainError("This payment session has expired.")

    async def create_checkout(self, context: PaymentContext) -> PaymentCheckout:
        """Create one checkout; use exact decimal JSON and explicitly disable recurrence."""
        minutes = max(1, math.ceil((context.expires_at - datetime.now(UTC)).total_seconds() / 60))
        payment = {
            "name": context.title[:100],
            "customerIdentifier": context.customer.external_id,
            "isPreAuth": False,
            "enableRecurring": False,
            "enableBNPL": False,
            "returnUrl": self._return_url,
        }
        # Inject only a validated decimal literal, never binary float money.
        row = json.dumps(payment)[:-1] + ',"amount":' + money_str(context.amount) + "}"
        body = (
            '{"paymentReferenceId":'
            + json.dumps(context.reference)
            + ',"paymentExpiredOnMinutes":'
            + str(minutes)
            + ',"customerPayments":['
            + row
            + "]}"
        )
        try:
            result = _CheckoutResponse.model_validate(
                await self._request("api/payments/customer-payment", body)
            ).customer_payments[0]
            if result.customer_identifier != context.customer.external_id:
                raise PaymentProviderUnavailableError()
            return PaymentCheckout(context.reference, self._safe_url(result.payment_url))
        except (ValidationError, ValueError) as exc:
            raise PaymentProviderUnavailableError() from exc

    async def get_payment_status(self, context: PaymentContext) -> PaymentStatus:
        """Require the exact reference, customer and amount in authenticated API results."""
        body = json.dumps(
            {
                "paymentReferenceId": context.reference,
                "customerIdentifier": context.customer.external_id,
            }
        )
        try:
            row = _StatusResponse.model_validate(
                await self._request("api/payments/customer-payment-status", body)
            ).customer_payments[0]
            if (
                row.payment_reference_id != context.reference
                or row.customer_identifier != context.customer.external_id
                or row.amount != context.amount
            ):
                raise PaymentProviderUnavailableError()
            return PaymentStatus(
                PaymentState.PAID if row.payment_status == 1 else PaymentState.PENDING,
                row.amount,
                self._safe_url(row.payment_url) if row.payment_url else None,
            )
        except (ValidationError, ValueError) as exc:
            raise PaymentProviderUnavailableError() from exc

    async def cancel_checkout(self, context: PaymentContext) -> None:
        """Close an unpaid checkout; unknown outcomes must be reconciled before replacement."""
        result = await self._request(
            "api/payments/cancel",
            json.dumps(
                {
                    "paymentReferenceId": context.reference,
                    "customerIdentifier": context.customer.external_id,
                }
            ),
            method="PUT",
        )
        if not isinstance(result, dict) or str(result.get("messageCode")) != "200":
            raise PaymentProviderUnavailableError()

    async def create_payment_link(
        self,
        *,
        reference: str,
        customer: PaymentCustomer,
        items: tuple[PaymentItem, ...],
        success_redirect_url: str | None,
        failure_redirect_url: str | None,
    ) -> PaymentCheckout:
        """Require durable checkout context rather than the simulator's item-only contract."""
        raise ValidationDomainError("Use the hosted payment-session service.")

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Fail closed: the supplied guide defines no Dhamen signature protocol."""
        return False
