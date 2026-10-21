from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import ForbiddenError
from app.repositories.courier_report_repository import CourierReportRepository
from app.services.courier_report_service import CourierReportService


def test_summary_statement_counts_defined_statuses_and_uses_one_aggregate_query():
    from app.models.enums import OrderStatus
    from app.repositories.courier_report_repository import CURRENT_COURIER_ORDER_STATUSES
    from sqlalchemy.dialects import postgresql

    courier_id = uuid4()
    statement = CourierReportRepository(AsyncMock())._summary_query(courier_id)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    params = compiled.params

    assert "orders.courier_id =" in sql
    assert "orders.status IN" in sql
    assert "orders.status =" in sql
    assert "transactions.reference_order_id IS NOT NULL" in sql
    assert "transactions.reference_invoice_id IS NOT NULL" in sql
    assert "transactions.status =" in sql
    assert "statement_timestamp()" in sql
    assert "LIMIT" not in sql
    assert courier_id in params.values()
    assert any(
        tuple(value) == CURRENT_COURIER_ORDER_STATUSES
        for value in params.values()
        if isinstance(value, (tuple, list))
    )
    assert set(CURRENT_COURIER_ORDER_STATUSES) == {
        OrderStatus.ASSIGNED,
        OrderStatus.WAITING_PAYMENT,
        OrderStatus.IN_PROGRESS,
        OrderStatus.DELIVERED,
        OrderStatus.DISPUTED,
    }
    assert OrderStatus.COMPLETED in params.values()


def test_summary_contract_requires_decimal_string_and_currency():
    from app.schemas.courier_report_schemas import CourierHomeSummary
    from pydantic import ValidationError

    values = {
        "current_orders_count": 0,
        "registered_orders_count": 0,
        "completed_orders_count": 0,
        "settled_earnings_total": "0.00",
        "currency": "SAR",
        "as_of": datetime(2026, 10, 21, 9, 30, tzinfo=UTC),
    }
    assert CourierHomeSummary.model_validate(values).settled_earnings_total == "0.00"

    with pytest.raises(ValidationError):
        CourierHomeSummary.model_validate({**values, "settled_earnings_total": 0.0})
    with pytest.raises(ValidationError):
        CourierHomeSummary.model_validate(
            {key: value for key, value in values.items() if key != "currency"}
        )


async def test_summary_query_uses_single_unpaged_snapshot_and_earnings_basis():
    from app.models.enums import OrderStatus, TransactionStatus, TransactionType, WalletType
    from sqlalchemy.dialects import postgresql

    session = AsyncMock()
    as_of = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
    session.execute.return_value.one.return_value = SimpleNamespace(
        current_orders_count=4,
        registered_orders_count=8,
        completed_orders_count=3,
        settled_earnings_total=Decimal("258.75"),
        currency="SAR",
        as_of=as_of,
    )
    courier_id = uuid4()

    result = await CourierReportRepository(session).summary(courier_id)

    assert result.current_orders_count == 4
    assert result.registered_orders_count == 8
    assert result.completed_orders_count == 3
    assert result.settled_earnings_total == Decimal("258.75")
    assert result.currency == "SAR"
    assert result.as_of == as_of
    session.execute.assert_awaited_once()
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "orders.courier_id =" in sql
    assert "transactions.reference_order_id IS NOT NULL" in sql
    assert "transactions.reference_invoice_id IS NOT NULL" in sql
    assert "transactions.created_at >=" not in sql
    assert "transactions.created_at <" not in sql
    assert "LIMIT" not in sql
    assert courier_id in compiled.params.values()
    assert OrderStatus.COMPLETED in compiled.params.values()
    assert TransactionType.ESCROW_RELEASE in compiled.params.values()
    assert TransactionStatus.SETTLED in compiled.params.values()
    assert WalletType.COURIER in compiled.params.values()


async def test_summary_empty_account_returns_zero_values_and_sar():
    session = AsyncMock()
    session.execute.return_value.one.return_value = SimpleNamespace(
        current_orders_count=None,
        registered_orders_count=None,
        completed_orders_count=None,
        settled_earnings_total=None,
        currency=None,
        as_of=datetime(2026, 10, 21, 9, 30, tzinfo=UTC),
    )

    result = await CourierReportRepository(session).summary(uuid4())

    assert result.current_orders_count == 0
    assert result.registered_orders_count == 0
    assert result.completed_orders_count == 0
    assert result.settled_earnings_total == Decimal("0.00")
    assert result.currency == "SAR"


async def test_summary_requires_eligible_courier_and_uses_actor_identity():
    courier_id = uuid4()
    reports = AsyncMock()
    reports.summary.return_value = SimpleNamespace(
        current_orders_count=2,
        registered_orders_count=5,
        completed_orders_count=3,
        settled_earnings_total=Decimal("125.50"),
        currency="SAR",
        as_of=datetime(2026, 10, 21, 9, 30, tzinfo=UTC),
    )
    eligibility = AsyncMock()

    result = await CourierReportService(reports, eligibility).summary(courier_id)

    eligibility.require_courier.assert_awaited_once_with(courier_id)
    reports.summary.assert_awaited_once_with(courier_id)
    assert result.current_orders_count == 2
    assert result.registered_orders_count == 5
    assert result.completed_orders_count == 3
    assert result.settled_earnings_total == "125.50"


async def test_summary_eligibility_failure_prevents_aggregate_read():
    reports, eligibility = AsyncMock(), AsyncMock()
    eligibility.require_courier.side_effect = ForbiddenError()

    with pytest.raises(ForbiddenError):
        await CourierReportService(reports, eligibility).summary(uuid4())

    reports.summary.assert_not_awaited()


@pytest.mark.parametrize("role", ["CUSTOMER", "ADMIN"])
async def test_summary_route_rejects_non_courier_roles(role, monkeypatch):
    from app.core.deps import Actor, get_db, require_auth
    from app.core.middleware import register_exception_handlers
    from app.models.enums import UserRole
    from app.routers import courier_report_routes
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(courier_report_routes.router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: Actor(uuid4(), UserRole(role), "test")
    service = AsyncMock()
    monkeypatch.setattr(courier_report_routes, "_service", lambda _db: service)

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/couriers/me/summary")

    assert response.status_code == 403
    service.summary.assert_not_awaited()


async def test_summary_route_returns_no_store_response_for_authenticated_courier(monkeypatch):
    from app.core.deps import Actor, get_db, require_auth
    from app.core.middleware import register_exception_handlers
    from app.models.enums import UserRole
    from app.routers import courier_report_routes
    from app.schemas.courier_report_schemas import CourierHomeSummary
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    actor_id = uuid4()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(courier_report_routes.router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: Actor(actor_id, UserRole.COURIER, "test")
    service = AsyncMock()
    service.summary.return_value = CourierHomeSummary(
        current_orders_count=4,
        registered_orders_count=8,
        completed_orders_count=3,
        settled_earnings_total="258.75",
        currency="SAR",
        as_of=datetime(2026, 10, 21, 9, 30, tzinfo=UTC),
    )
    monkeypatch.setattr(courier_report_routes, "_service", lambda _db: service)

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/couriers/me/summary")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "current_orders_count": 4,
        "registered_orders_count": 8,
        "completed_orders_count": 3,
        "settled_earnings_total": "258.75",
        "currency": "SAR",
        "as_of": "2026-10-21T09:30:00Z",
    }
    service.summary.assert_awaited_once_with(actor_id)
