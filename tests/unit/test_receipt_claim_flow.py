"""Receipt delivery closes the claim transaction before calling the provider."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.services import receipt_service

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_email_send_occurs_between_claim_and_completion_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice_id = uuid.uuid4()
    order_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    invoice = SimpleNamespace(
        id=invoice_id,
        order_id=order_id,
        currency="SAR",
        items_net_amount=Decimal("10.00"),
        courier_fee_amount=Decimal("1.00"),
        service_fee_amount=Decimal("1.00"),
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("1.80"),
        total_amount=Decimal("13.80"),
        promo_code_snapshot=None,
        paid_at=None,
        issued_at=None,
        status="PAID",
    )
    active_sessions = 0
    events: list[str] = []

    class Session:
        async def __aenter__(self) -> Session:
            nonlocal active_sessions
            active_sessions += 1
            return self

        async def __aexit__(self, *_args: object) -> None:
            nonlocal active_sessions
            active_sessions -= 1

        async def commit(self) -> None:
            events.append("commit")

    class Invoices:
        async def claim_receipt(self, *_args: object, **_kwargs: object) -> object:
            events.append("claim")
            return invoice

        async def list_items(self, *_args):
            return []

        async def complete_receipt(self, *_args: object, **_kwargs: object) -> bool:
            events.append("complete")
            return True

    class Email:
        async def send_transactional(self, *_args: object) -> None:
            assert active_sessions == 0
            events.append("send")

    monkeypatch.setattr(receipt_service, "InvoiceRepository", lambda session: Invoices())
    monkeypatch.setattr(
        receipt_service,
        "OrderRepository",
        lambda session: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(customer_id=customer_id))
        ),
    )
    monkeypatch.setattr(
        receipt_service,
        "UserRepository",
        lambda session: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(email="a@example.com"))
        ),
    )

    service = receipt_service.ReceiptService(
        factory=lambda: Session(), email=Email(), settings=make_test_settings()
    )
    assert await service.send_receipt(invoice_id)
    assert events == ["claim", "commit", "send", "complete", "commit"]


@pytest.mark.asyncio
async def test_provider_timeout_leaves_claim_for_later_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(receipt_service, "_SEND_TIMEOUT_SECONDS", 0.001)
    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        currency="SAR",
        items_net_amount=Decimal("10.00"),
        courier_fee_amount=Decimal("1.00"),
        service_fee_amount=Decimal("1.00"),
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("1.80"),
        total_amount=Decimal("13.80"),
        promo_code_snapshot=None,
        paid_at=None,
        issued_at=None,
        status="PAID",
    )
    complete = AsyncMock()

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(
        receipt_service,
        "InvoiceRepository",
        lambda session: SimpleNamespace(
            claim_receipt=AsyncMock(return_value=invoice),
            complete_receipt=complete,
            list_items=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(
        receipt_service,
        "OrderRepository",
        lambda session: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(customer_id=uuid.uuid4()))
        ),
    )
    monkeypatch.setattr(
        receipt_service,
        "UserRepository",
        lambda session: SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(email="a@example.com"))
        ),
    )

    async def slow_send(*_args: object) -> None:
        await asyncio.sleep(1)

    email = SimpleNamespace(send_transactional=slow_send)
    service = receipt_service.ReceiptService(
        factory=lambda: Session(), email=email, settings=make_test_settings()
    )

    with pytest.raises(TimeoutError):
        await service.send_receipt(invoice.id)
    complete.assert_not_awaited()
