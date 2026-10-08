import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.core.exceptions import DomainError
from app.integrations.payments.base import PaymentContext, PaymentCustomer, PaymentState
from app.integrations.payments.dhamen import DhamenPaymentClient
from pydantic import ValidationError

from tests.conftest import make_test_settings


def context() -> PaymentContext:
    return PaymentContext(
        reference="83a75b60-19ac-42d9-b5ac-5ab7f47c8c02",
        customer=PaymentCustomer(
            "af81ab60-19ac-42d9-b5ac-5ab7f47c8c02", "Customer", "+966501234567", None
        ),
        amount=Decimal("123.45"),
        currency="SAR",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def client(handler: object) -> DhamenPaymentClient:
    return DhamenPaymentClient(
        base_url="https://api.example.com",
        app_id="test-app-id",
        app_key="test-app-key",
        client_id="test-client-id",
        checkout_hosts=("pay.example.com",),
        return_url="https://giftly.example.com/payment-return",
        transport=httpx.MockTransport(handler),
    )


async def test_checkout_uses_exact_amount_and_one_time_payment() -> None:
    ctx = context()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/payments/customer-payment"
        assert request.headers["api-version"] == "2"
        assert request.headers["App-key"] == "test-app-key"
        body = json.loads(request.content, parse_float=Decimal)
        assert body["paymentReferenceId"] == ctx.reference
        payment = body["customerPayments"][0]
        assert payment["amount"] == Decimal("123.45")
        assert isinstance(payment["amount"], Decimal)
        assert payment["enableRecurring"] is False
        assert payment["isPreAuth"] is False
        assert payment["enableBNPL"] is False
        assert "supplierId" not in payment
        assert "items" not in payment
        return httpx.Response(
            200,
            json={
                "customerPayments": [
                    {
                        "customerIdentifier": ctx.customer.external_id,
                        "paymentUrl": "https://pay.example.com/pay/test",
                        "invoiceId": "provider-invoice",
                    }
                ]
            },
        )

    gateway = client(handler)
    try:
        checkout = await gateway.create_checkout(ctx)
        assert checkout.payment_link_id == ctx.reference
        assert checkout.payment_url == "https://pay.example.com/pay/test"
    finally:
        await gateway.aclose()


@pytest.mark.parametrize("status, expected", [(0, PaymentState.PENDING), (1, PaymentState.PAID)])
async def test_status_is_bound_to_reference_customer_and_exact_amount(
    status: int, expected: PaymentState
) -> None:
    ctx = context()
    gateway = client(
        lambda request: httpx.Response(
            200,
            content=json.dumps(
                {
                    "customerPayments": [
                        {
                            "paymentReferenceId": ctx.reference,
                            "customerIdentifier": ctx.customer.external_id,
                            "amount": 123.45,
                            "paymentStatus": status,
                            "paymentUrl": "https://pay.example.com/pay/test",
                        }
                    ]
                }
            ),
        )
    )
    try:
        result = await gateway.get_payment_status(ctx)
        assert result.state is expected
        assert result.amount == ctx.amount
    finally:
        await gateway.aclose()


@pytest.mark.parametrize(
    "patch",
    [
        {"amount": "999.00"},
        {"customerIdentifier": "other"},
        {"paymentReferenceId": "other"},
        {"paymentStatus": 7},
        {"paymentStatus": True},
        {"paymentStatus": False},
        {"paymentStatus": 1.0},
    ],
)
async def test_status_mismatch_or_unknown_state_is_rejected(patch: dict[str, object]) -> None:
    ctx = context()
    payment = {
        "paymentReferenceId": ctx.reference,
        "customerIdentifier": ctx.customer.external_id,
        "amount": "123.45",
        "paymentStatus": 1,
    } | patch
    gateway = client(lambda request: httpx.Response(200, json={"customerPayments": [payment]}))
    try:
        with pytest.raises(DomainError):
            await gateway.get_payment_status(ctx)
    finally:
        await gateway.aclose()


async def test_creation_timeout_is_unknown_and_never_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    gateway = client(handler)
    try:
        with pytest.raises(DomainError) as caught:
            await gateway.create_checkout(context())
        assert caught.value.code == "PAYMENT_PROVIDER_UNAVAILABLE"
        assert calls == 1
    finally:
        await gateway.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "http://pay.example.com/pay/test",
        "https://evil.example/pay",
        "https://user:secret@pay.example.com/pay",
    ],
)
async def test_unsafe_checkout_urls_are_rejected(url: str) -> None:
    ctx = context()
    gateway = client(
        lambda request: httpx.Response(
            200,
            json={
                "customerPayments": [
                    {
                        "customerIdentifier": ctx.customer.external_id,
                        "paymentUrl": url,
                        "invoiceId": "test",
                    }
                ]
            },
        )
    )
    try:
        with pytest.raises(DomainError):
            await gateway.create_checkout(ctx)
    finally:
        await gateway.aclose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"PAYMENT_PROVIDER": "dhamen"},
        {"PAYMENT_PROVIDER": "dhamen", "DHAMEN_ENVIRONMENT": "production"},
    ],
)
def test_dhamen_config_fails_closed_without_valid_mode_and_credentials(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_test_settings(**overrides)


def test_production_rejects_dhamen_until_live_contract_is_verified() -> None:
    with pytest.raises(ValidationError, match="Production Dhamen payments remain disabled"):
        make_test_settings(ENVIRONMENT="production", PAYMENT_PROVIDER="dhamen")
