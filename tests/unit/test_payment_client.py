"""Contract tests for the local payment simulator; no vendor protocol is implied."""

from decimal import Decimal

from app.core.config import Environment
from app.integrations.payments.base import PaymentCustomer, PaymentItem
from app.integrations.payments.fake import FakePaymentClient


async def test_fake_payment_returns_a_local_payment_link_and_signed_webhook() -> None:
    client = FakePaymentClient(Environment.TEST)
    checkout = await client.create_payment_link(
        reference="intent-1",
        customer=PaymentCustomer("customer-1", "Ada", "+966501234567", None),
        items=(PaymentItem("Wallet top-up", None, Decimal("100.00")),),
        success_redirect_url=None,
        failure_redirect_url=None,
    )
    body = b'{"event_type":"PAYMENT_SUCCEEDED"}'

    assert checkout.payment_link_id.startswith("FAKE-PAYMENT-LINK-")
    assert "payment_link_id=" + checkout.payment_link_id in checkout.payment_url
    assert client.verify_webhook_signature(body, client.sign(body, timestamp="1720000000"))
