from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.admin import router as admin_routes
from app.admin import table_router
from app.admin.deps import AdminRedirect, get_db
from app.admin.router import router
from app.core.exceptions import ConflictError, DomainError
from app.core.security import make_csrf_token
from app.models import Base
from app.services.admin_table_fields import form_fields
from app.services.admin_table_service import TableForm
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings


def make_app(monkeypatch, *, step_up=True):
    app = FastAPI()
    app.state.settings = settings = make_test_settings()
    app.include_router(router)

    async def no_database():
        yield None

    app.dependency_overrides[get_db] = no_database

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"message": exc.message}, status_code=exc.status_code)

    @app.exception_handler(AdminRedirect)
    async def redirect(request, exc):
        return RedirectResponse(exc.location)

    csrf = make_csrf_token("test-session-hash", settings.ADMIN_SESSION_SECRET.get_secret_value())
    ctx = SimpleNamespace(
        session_row=SimpleNamespace(id=uuid4(), session_token_hash="test-session-hash"),
        admin=SimpleNamespace(id=uuid4()),
        csrf_token=csrf,
        auth=SimpleNamespace(has_step_up=AsyncMock(return_value=step_up)),
        tables=SimpleNamespace(save=AsyncMock(return_value=uuid4()), delete=AsyncMock()),
    )
    monkeypatch.setattr(table_router, "require_admin", AsyncMock(return_value=ctx))
    return app, ctx


@pytest.mark.parametrize("operation", ["new", "edit", "delete"])
async def test_every_mutation_rejects_forged_csrf(monkeypatch, operation):
    app, ctx = make_app(monkeypatch)
    suffix = "new" if operation == "new" else f"{uuid4()}/{operation}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/admin/tables/users/{suffix}", data={"csrf_token": "forged"})
    assert response.status_code == 403
    ctx.tables.save.assert_not_awaited()
    ctx.tables.delete.assert_not_awaited()


@pytest.mark.parametrize("valid", [True, False])
async def test_logout_requires_the_current_session_csrf(monkeypatch, valid):
    app, ctx = make_app(monkeypatch)
    ctx.auth.logout = AsyncMock()
    monkeypatch.setattr(admin_routes, "_ctx", AsyncMock(return_value=ctx))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"admin_session": "test-cookie"},
    ) as client:
        response = await client.post(
            "/admin/logout", data={"csrf_token": ctx.csrf_token if valid else "forged"}
        )
    assert response.status_code == (303 if valid else 403)
    assert ctx.auth.logout.await_count == int(valid)


async def test_mutations_require_recent_password_confirmation(monkeypatch):
    app, ctx = make_app(monkeypatch, step_up=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/tables/users/new", data={"csrf_token": ctx.csrf_token})
    assert response.status_code == 403
    ctx.tables.save.assert_not_awaited()


async def test_authenticated_create_passes_trusted_actor_and_session(monkeypatch):
    app, ctx = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/tables/users/new",
            data={"csrf_token": ctx.csrf_token, "phone": "123", "role": "CUSTOMER"},
        )
    assert response.status_code == 303
    assert ctx.tables.save.call_args.kwargs["admin_id"] == ctx.admin.id
    assert ctx.tables.save.call_args.kwargs["session_id"] == ctx.session_row.id


async def test_all_table_forms_render_relationship_widgets_and_secret_fields(monkeypatch):
    app, ctx = make_app(monkeypatch)

    async def form(name, record_id=None):
        return TableForm(name, form_fields(Base.metadata.tables[name], None), record_id, "")

    ctx.tables.form = form
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for table in Base.metadata.tables.values():
            response = await client.get(f"/admin/tables/{table.name}/new")
            assert response.status_code == 200, table.name
            assert response.headers["cache-control"] == "no-store"
            for column in table.c:
                if column.foreign_keys:
                    assert (
                        f'data-url="/admin/relationships/{table.name}/{column.name}"'
                        in response.text
                    )


async def test_stale_write_response_shows_current_values_not_stale_submission(monkeypatch):
    app, ctx = make_app(monkeypatch)
    identifier = uuid4()
    current = {"title": "Concurrent change"}
    ctx.tables.form = AsyncMock(
        return_value=TableForm(
            "featured_gifts",
            form_fields(Base.metadata.tables["featured_gifts"], current),
            identifier,
            "latest-revision",
        )
    )
    ctx.tables.save.side_effect = ConflictError("Reload the record.")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/admin/tables/featured_gifts/{identifier}/edit",
            data={"csrf_token": ctx.csrf_token, "title": "Stale value", "revision": "old-revision"},
        )
    assert response.status_code == 409
    assert "Concurrent change" in response.text
    assert "Stale value" not in response.text


async def test_oversized_form_rejected_before_service_write(monkeypatch):
    app, ctx = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/tables/users/new",
            content=b"x=" + b"x" * 262_144,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 422
    ctx.tables.save.assert_not_awaited()
