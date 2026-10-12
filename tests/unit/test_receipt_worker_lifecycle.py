"""Receipt sweeps release clients they construct themselves."""

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
    monkeypatch.setattr(InvoiceRepository, "list_receipt_pending", AsyncMock(return_value=[]))
    session = AsyncMock()
    session.__aenter__.return_value = session
    factory = Mock(return_value=session)

    assert await receipts.send_pending_receipts(factory=factory, settings=make_test_settings()) == 0
    email.aclose.assert_awaited_once()
    sms.aclose.assert_not_awaited()
    push.aclose.assert_not_awaited()
