"""Receipt sweeps release clients they construct themselves."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.repositories.invoice_repository import InvoiceRepository
from app.workers import receipts

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_receipt_sweep_closes_owned_integration_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = SimpleNamespace(aclose=AsyncMock())
    sms = SimpleNamespace(aclose=AsyncMock())
    push = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(receipts, "build_email_client", lambda settings: email)
    monkeypatch.setattr(InvoiceRepository, "list_due_receipt_ids", AsyncMock(return_value=[]))
    session = AsyncMock()
    session.__aenter__.return_value = session
    factory = Mock(return_value=session)

    assert await receipts.send_pending_receipts(factory=factory, settings=make_test_settings()) == 0
    email.aclose.assert_awaited_once()
    sms.aclose.assert_not_awaited()
    push.aclose.assert_not_awaited()


async def test_receipt_sweep_leaves_pending_rows_when_send_budget_is_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice_id = uuid.uuid4()
    monkeypatch.setattr(
        InvoiceRepository, "list_due_receipt_ids", AsyncMock(return_value=[invoice_id])
    )
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(receipts.ReceiptService, "send_receipt", send)
    session = AsyncMock()
    session.__aenter__.return_value = session
    factory = Mock(return_value=session)
    settings = make_test_settings()

    assert (
        await receipts.send_pending_receipts(
            factory=factory,
            settings=settings,
            deadline=asyncio.get_running_loop().time() + 10,
        )
        == 0
    )
    send.assert_not_awaited()
    assert await receipts.send_pending_receipts(factory=factory, settings=settings) == 1
    send.assert_awaited_once_with(invoice_id)
