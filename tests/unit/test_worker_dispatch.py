from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.worker_dispatch import (
    WorkerTask,
    dispatch_worker_wakeups,
    request_worker_wakeup,
)


@pytest.mark.asyncio
async def test_worker_wakeups_are_deduplicated_and_queue_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(info={})
    request_worker_wakeup(session, WorkerTask.INVOICE_RECEIPTS)
    request_worker_wakeup(session, WorkerTask.INVOICE_RECEIPTS)
    request_worker_wakeup(session, WorkerTask.CHAT_NOTIFICATIONS)

    enqueue = AsyncMock(side_effect=[None, ConnectionError("Redis unavailable")])
    monkeypatch.setattr("app.core.worker_dispatch._enqueue_worker_task", enqueue)

    all_queued = await dispatch_worker_wakeups(session)

    assert all_queued == {WorkerTask.CHAT_NOTIFICATIONS}
    assert enqueue.await_count == 2
    assert {call.args[0] for call in enqueue.await_args_list} == {
        WorkerTask.INVOICE_RECEIPTS,
        WorkerTask.CHAT_NOTIFICATIONS,
    }
    assert "worker_wakeups" not in session.info


@pytest.mark.asyncio
async def test_get_db_dispatches_wakeups_only_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import deps

    events: list[str] = []

    class Session:
        info: dict[str, object] = {}

        async def execute(self, _statement: object) -> None:
            return None

        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class SessionContext:
        def __init__(self) -> None:
            self.session = Session()

        async def __aenter__(self) -> Session:
            return self.session

        async def __aexit__(self, *_args: object) -> None:
            return None

    session_context = SessionContext()
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/orders"),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=lambda: session_context)),
    )

    async def dispatch(session: Session) -> bool:
        assert events == ["commit"]
        assert session.info["worker_wakeups"] == {WorkerTask.ORDER_NOTIFICATIONS.value}
        events.append("dispatch")
        return True

    monkeypatch.setattr(deps, "mark_request_transaction", AsyncMock())
    monkeypatch.setattr(deps, "emit_committed_audit_events", lambda _session: None)
    monkeypatch.setattr(deps, "dispatch_worker_wakeups", dispatch)

    generator = deps.get_db(request)
    session = await generator.__anext__()
    request_worker_wakeup(session, WorkerTask.ORDER_NOTIFICATIONS)
    with pytest.raises(StopAsyncIteration):
        await generator.__anext__()

    assert events == ["commit", "dispatch"]


@pytest.mark.asyncio
async def test_get_db_does_not_dispatch_after_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import deps

    class Session:
        info: dict[str, object] = {}

        async def execute(self, _statement: object) -> None:
            return None

        async def commit(self) -> None:
            raise RuntimeError("database commit failed")

        async def rollback(self) -> None:
            self.info.clear()

    class SessionContext:
        def __init__(self) -> None:
            self.session = Session()

        async def __aenter__(self) -> Session:
            return self.session

        async def __aexit__(self, *_args: object) -> None:
            return None

    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/orders"),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=SessionContext)),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(deps, "mark_request_transaction", AsyncMock())
    monkeypatch.setattr(deps, "emit_committed_audit_events", lambda _session: None)
    monkeypatch.setattr(deps, "dispatch_worker_wakeups", dispatch)

    generator = deps.get_db(request)
    session = await generator.__anext__()
    request_worker_wakeup(session, WorkerTask.ORDER_NOTIFICATIONS)
    with pytest.raises(RuntimeError, match="database commit failed"):
        await generator.__anext__()

    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_invoice_route_requests_receipt_wakeup(monkeypatch: pytest.MonkeyPatch) -> None:
    from decimal import Decimal

    from app.core.deps import Actor
    from app.models.enums import UserRole
    from app.routers import invoices
    from app.schemas.payments import PayInvoiceRequest
    from app.services.payment_service import PayResult

    actor = Actor(uuid4(), UserRole.CUSTOMER, "test-session")
    invoice_id = uuid4()
    service = AsyncMock()
    service.pay_invoice.return_value = PayResult(
        invoice_id=invoice_id,
        status="PAID",
        amount_from_wallet=Decimal("25.00"),
        amount_from_gateway=Decimal("0.00"),
        payment_url=None,
    )
    monkeypatch.setattr(invoices, "build_payment_service", lambda **_kwargs: service)
    monkeypatch.setattr(invoices, "get_redis", lambda _request: object())
    monkeypatch.setattr(invoices, "get_settings", lambda _request: object())
    db = SimpleNamespace(info={})

    result = await invoices.pay_invoice(
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(clients=SimpleNamespace(gateway=object())))
        ),
        db,
        invoice_id,
        actor,
        PayInvoiceRequest(),
    )

    assert result.status == "PAID"
    assert db.info["worker_wakeups"] == {WorkerTask.INVOICE_RECEIPTS.value}
