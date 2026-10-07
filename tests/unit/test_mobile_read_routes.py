from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.deps import Actor, get_db, require_auth
from app.core.middleware import register_exception_handlers
from app.models.enums import UserRole
from app.routers import invoices, order_media, wallets
from app.schemas.invoice_list import InvoicePage
from app.schemas.wallet_statement import StatementTotals, WalletStatement
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.parametrize(
    "path,params",
    [
        ("/api/invoices", {"from_date": "bad"}),
        ("/api/invoices", {"from_date": "2026-10-31", "to_date": "2026-10-01"}),
        ("/api/invoices", {"status": "INVALID"}),
        ("/api/invoices", {"limit": "101"}),
        ("/api/wallets/me/statement", {}),
        (
            "/api/wallets/me/statement",
            {"from_date": "2026-10-01T12:00:00", "to_date": "2026-10-31"},
        ),
        ("/api/orders/11111111-1111-4111-8111-111111111111/media", {"purpose": "PROFILE_AVATAR"}),
        ("/api/orders/11111111-1111-4111-8111-111111111111/media", {"limit": "0"}),
    ],
)
async def test_new_read_routes_reject_invalid_query_before_service(monkeypatch, path, params):
    app = FastAPI()
    for router in (invoices.router, wallets.router, order_media.router):
        app.include_router(router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: Actor(uuid4(), UserRole.CUSTOMER, "test")
    service = AsyncMock()
    monkeypatch.setattr(invoices, "MobileInvoiceService", Mock(return_value=service))
    monkeypatch.setattr(wallets, "WalletStatementService", Mock(return_value=service))
    monkeypatch.setattr(order_media, "OrderMediaReadService", Mock(return_value=service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get(path, params=params)
    assert result.status_code == 422, result.text
    service.list.assert_not_awaited()
    service.read.assert_not_awaited()


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.COURIER, UserRole.ADMIN])
async def test_invoice_list_role_boundary_and_private_cache_control(monkeypatch, role):
    app = FastAPI()
    app.include_router(invoices.router)
    register_exception_handlers(app)
    actor = Actor(uuid4(), role, "test")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: actor
    service = AsyncMock()
    service.list.return_value = InvoicePage(items=[], next_cursor=None)
    monkeypatch.setattr(invoices, "MobileInvoiceService", Mock(return_value=service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/api/invoices")
    if role is UserRole.ADMIN:
        assert result.status_code == 403
        service.list.assert_not_awaited()
    else:
        assert result.status_code == 200, result.text
        assert result.json() == {"items": [], "next_cursor": None}
        assert result.headers["cache-control"] == "private, no-store"
        assert service.list.call_args.args == (actor.id,)


async def test_wallet_statement_http_money_and_empty_state(monkeypatch):
    from datetime import date

    app = FastAPI()
    app.include_router(wallets.router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: Actor(uuid4(), UserRole.COURIER, "test")
    service = AsyncMock()
    service.read.return_value = WalletStatement(
        items=[],
        next_cursor=None,
        currency="SAR",
        as_of=datetime.now(UTC),
        from_date=date(2026, 10, 1),
        to_date=date(2026, 10, 31),
        totals=StatementTotals(**{field: "0.00" for field in StatementTotals.model_fields}),
    )
    monkeypatch.setattr(wallets, "WalletStatementService", Mock(return_value=service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get(
            "/api/wallets/me/statement?from_date=2026-10-01&to_date=2026-10-31"
        )
    assert result.status_code == 200, result.text
    assert result.json()["items"] == [] and result.json()["next_cursor"] is None
    assert result.json()["totals"]["settled_net"] == "0.00"
    assert result.headers["cache-control"] == "private, no-store"
