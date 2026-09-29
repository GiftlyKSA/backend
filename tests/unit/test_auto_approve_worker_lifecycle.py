"""Auto-approve sweeps reuse and close their owned integration clients."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.repositories.order_repository import OrderRepository
from app.workers import auto_approve

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_auto_approve_sweep_reuses_and_closes_clients_after_order_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = SimpleNamespace(
        gateway=SimpleNamespace(aclose=AsyncMock()),
        email=SimpleNamespace(aclose=AsyncMock()),
        sms=SimpleNamespace(aclose=AsyncMock()),
        push=SimpleNamespace(aclose=AsyncMock()),
        storage=SimpleNamespace(aclose=AsyncMock()),
    )
    build_clients = Mock(return_value=clients)
    monkeypatch.setattr(auto_approve, "build_clients", build_clients)
    order_ids = [uuid4(), uuid4()]
    monkeypatch.setattr(
        OrderRepository,
        "list_auto_approve_due",
        AsyncMock(return_value=[SimpleNamespace(id=order_id) for order_id in order_ids]),
    )
    service = SimpleNamespace(
        auto_approve_cutoff=Mock(),
        auto_approve=AsyncMock(side_effect=[RuntimeError("one order failed"), True]),
    )
    storage_clients: list[object] = []

    def service_factory(session: object, settings: object, storage: object) -> object:
        storage_clients.append(storage)
        return service

    monkeypatch.setattr(auto_approve, "_service", service_factory)
    session = AsyncMock()
    session.__aenter__.return_value = session
    factory = Mock(return_value=session)
    settings = make_test_settings()

    assert await auto_approve.auto_approve_delivered(factory=factory, settings=settings) == 1
    build_clients.assert_called_once_with(settings)
    assert storage_clients == [clients.storage, clients.storage, clients.storage]
    service.auto_approve.assert_awaited()
    session.rollback.assert_awaited_once()
    for client in (clients.gateway, clients.email, clients.sms, clients.push, clients.storage):
        client.aclose.assert_awaited_once()
