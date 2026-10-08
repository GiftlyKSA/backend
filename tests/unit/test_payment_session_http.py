from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.core.deps import Actor, get_db, require_auth
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.middleware import register_exception_handlers
from app.models.enums import UserRole
from app.routers import payment_sessions
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.unit.test_payment_session_contracts import row

_PATHS = [
    ("/api/payment-sessions/{id}", "get", "get_payment_session"),
    ("/api/orders/{id}/payment-session", "get", "get_order_payment_session"),
    ("/api/wallets/me/topup-session", "get", "get_topup_payment_session"),
    ("/api/payment-sessions/{id}/refresh", "post", "refresh_payment_session"),
    ("/api/payment-sessions/{id}/cancel", "post", "cancel_payment_session"),
]


@pytest.mark.parametrize("path,method,operation", _PATHS)
@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.COURIER, UserRole.ADMIN])
async def test_session_routes_are_owned_private_and_role_restricted(
    monkeypatch, path, method, operation, role
):
    app = FastAPI()
    for router in (
        payment_sessions.router,
        payment_sessions.order_router,
        payment_sessions.wallet_router,
    ):
        app.include_router(router)
    register_exception_handlers(app)
    actor = Actor(uuid4(), role, "test")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: actor
    eligibility = AsyncMock()
    monkeypatch.setattr(
        payment_sessions, "CourierEligibilityService", Mock(return_value=eligibility), raising=False
    )
    service = AsyncMock()
    getattr(service, operation).return_value = row()
    monkeypatch.setattr(payment_sessions, "_service", Mock(return_value=service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.request(method, path.format(id=uuid4()))
    if role is UserRole.ADMIN:
        assert result.status_code == 403
        getattr(service, operation).assert_not_awaited()
    else:
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "private, no-store"
        assert getattr(service, operation).call_args.kwargs["user_id"] == actor.id
        assert result.json()["amount_from_gateway"] == "100.00"


@pytest.mark.parametrize("path,method,operation", _PATHS)
async def test_missing_or_foreign_session_preserves_safe_not_found(
    monkeypatch, path, method, operation
):
    app = FastAPI()
    for router in (
        payment_sessions.router,
        payment_sessions.order_router,
        payment_sessions.wallet_router,
    ):
        app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: Actor(uuid4(), UserRole.CUSTOMER, "test")
    eligibility = AsyncMock()
    monkeypatch.setattr(
        payment_sessions, "CourierEligibilityService", Mock(return_value=eligibility), raising=False
    )
    service = AsyncMock()
    getattr(service, operation).side_effect = NotFoundError("Payment session not found.")
    monkeypatch.setattr(payment_sessions, "_service", Mock(return_value=service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.request(method, path.format(id=uuid4()))
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "NOT_FOUND"


async def test_revoked_courier_eligibility_blocks_session_operations(monkeypatch):
    app = FastAPI()
    app.include_router(payment_sessions.router)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[require_auth] = lambda: Actor(uuid4(), UserRole.COURIER, "test")
    eligibility = AsyncMock()
    eligibility.require_eligible_actor.side_effect = ForbiddenError("Courier is not verified.")
    monkeypatch.setattr(
        payment_sessions, "CourierEligibilityService", Mock(return_value=eligibility), raising=False
    )
    service = AsyncMock()
    service.get_payment_session.return_value = row()
    monkeypatch.setattr(payment_sessions, "_service", Mock(return_value=service))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/payment-sessions/{uuid4()}")
    assert response.status_code == 403
    service.get_payment_session.assert_not_awaited()
