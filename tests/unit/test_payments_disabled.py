"""Production payment suspension must precede all side effects."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from app.core.config import Environment
from app.core.exceptions import DomainError
from app.integrations.payments.base import PaymentCustomer, PaymentItem
from app.integrations.payments.disabled import DisabledPaymentClient
from app.main import create_app
from app.services.payment_service import PaymentService

from tests.conftest import make_test_settings


@pytest.mark.parametrize("operation", ["topup", "invoice", "webhook"])
async def test_production_payments_fail_before_side_effects(operation: str) -> None:
    settings = make_test_settings().model_copy(update={"ENVIRONMENT": Environment.PRODUCTION})
    collaborators = {
        name: AsyncMock()
        for name in ("payments", "invoices", "orders", "wallets", "money", "promos", "users")
    }
    gateway = Mock()
    redis = AsyncMock()
    service = PaymentService(**collaborators, gateway=gateway, redis=redis, settings=settings)

    with pytest.raises(DomainError) as caught:
        if operation == "topup":
            await service.create_topup(user_id=uuid.uuid4(), amount=Decimal("100.00"))
        elif operation == "invoice":
            await service.pay_invoice(invoice_id=uuid.uuid4(), customer_id=uuid.uuid4())
        else:
            await service.handle_webhook(raw_body=b"{}", signature="")

    assert caught.value.code == "PAYMENTS_DISABLED"
    assert caught.value.status_code == 503
    assert all(not collaborator.mock_calls for collaborator in collaborators.values())
    assert not gateway.mock_calls
    assert not redis.mock_calls


async def test_disabled_client_rejects_checkout_and_all_signatures() -> None:
    client = DisabledPaymentClient()
    assert not client.verify_webhook_signature(b"{}", "")
    assert not client.verify_webhook_signature(b"{}", "t=1720000000,v1=anything")
    with pytest.raises(DomainError, match="temporarily unavailable"):
        await client.create_payment_link(
            reference="test",
            customer=PaymentCustomer("1", "Test", "+966501234567", None),
            items=(PaymentItem("Top-up", None, Decimal("100.00")),),
            success_redirect_url=None,
            failure_redirect_url=None,
        )


@pytest.mark.parametrize("environment", list(Environment))
def test_simulation_routes_never_register_in_production(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.main.build_clients", lambda settings: Mock())
    settings = make_test_settings().model_copy(update={"ENVIRONMENT": environment})
    paths = create_app(settings).openapi()["paths"]
    assert ("/api/webhooks/simulation" in paths) == (environment is not Environment.PRODUCTION)
    assert ("/api/dev/simulation/simulate" in paths) == (environment is Environment.DEVELOPMENT)
