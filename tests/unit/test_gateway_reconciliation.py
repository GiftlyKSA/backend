from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.config import Environment
from app.models.enums import PaymentIntentStatus

from tests.conftest import make_test_settings


async def test_default_and_production_reconciliation_never_contact_provider(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: make_test_settings())
    from app.workers.gateway_reconciliation import reconcile_gateway_payments

    build_client = Mock()
    monkeypatch.setattr("app.workers.gateway_reconciliation.build_payment_client", build_client)
    assert await reconcile_gateway_payments(settings=make_test_settings()) == 0
    settings = make_test_settings().model_copy(
        update={"ENVIRONMENT": Environment.PRODUCTION, "PAYMENT_PROVIDER": "dhamen"}
    )
    assert await reconcile_gateway_payments(settings=settings) == 0
    build_client.assert_not_called()


@pytest.mark.parametrize(
    "minutes_old, status, should_cancel",
    [
        (29, PaymentIntentStatus.NEW, False),
        (30, PaymentIntentStatus.NEW, True),
        (31, PaymentIntentStatus.PAID, False),
    ],
)
async def test_worker_closes_only_unpaid_sessions_at_thirty_minutes(
    monkeypatch, minutes_old, status, should_cancel
) -> None:
    from app.workers import gateway_reconciliation as worker

    intent = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        reference_invoice_id=uuid4(),
        status=status,
        checkout_provider="DHAMEN",
        checkout_state="ACTIVE",
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_old),
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    repository = AsyncMock()
    repository.get_intent.return_value = intent
    svc = AsyncMock()
    svc.refresh_payment_session.return_value = intent
    svc.cancel_payment_session.return_value = SimpleNamespace(
        status=PaymentIntentStatus.CANCELLED, reference_invoice_id=intent.reference_invoice_id
    )
    expiry = AsyncMock()
    monkeypatch.setattr(worker, "PaymentRepository", Mock(return_value=repository))
    monkeypatch.setattr(worker, "build_payment_service", Mock(return_value=svc))
    monkeypatch.setattr(worker, "ExpiryService", Mock(return_value=expiry))
    await worker._reconcile_one(AsyncMock(), intent.id, make_test_settings(), Mock(), AsyncMock())
    assert svc.cancel_payment_session.await_count == int(should_cancel)
    assert expiry.expire_invoice.await_count == int(should_cancel)
