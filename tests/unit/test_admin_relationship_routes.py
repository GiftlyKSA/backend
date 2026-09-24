from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.admin.deps import AdminRedirect, get_db
from app.admin.router import router
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from httpx import ASGITransport, AsyncClient


async def test_relationship_endpoint_requires_admin(monkeypatch):
    from app.admin import router as module

    app = FastAPI()
    app.include_router(router)

    async def no_database():
        yield None

    async def denied(*args):
        raise AdminRedirect()

    @app.exception_handler(AdminRedirect)
    async def redirect(request: Request, exc: AdminRedirect):
        return RedirectResponse(exc.location)

    app.dependency_overrides[get_db] = no_database
    monkeypatch.setattr(module, "_ctx", denied)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/relationships/orders/customer_id")
    assert response.status_code == 307
    assert response.headers["location"] == "/admin/login"


async def test_lookup_is_private_and_query_length_is_bounded(monkeypatch):
    from app.admin import router as module

    app = FastAPI()
    app.include_router(router)

    async def no_database():
        yield None

    service = SimpleNamespace(
        relationship_choices=AsyncMock(return_value={"items": [], "next_cursor": None})
    )
    monkeypatch.setattr(module, "_ctx", AsyncMock(return_value=SimpleNamespace(service=service)))
    app.dependency_overrides[get_db] = no_database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/relationships/orders/customer_id")
        oversized = await client.get(
            "/admin/relationships/orders/customer_id", params={"search": "x" * 101}
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert oversized.status_code == 422
    assert service.relationship_choices.await_count == 1
