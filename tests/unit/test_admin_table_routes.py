import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
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


def make_app(monkeypatch):
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
        auth=SimpleNamespace(),
        tables=SimpleNamespace(save=AsyncMock(return_value=uuid4()), delete=AsyncMock()),
    )
    monkeypatch.setattr(table_router, "require_admin", AsyncMock(return_value=ctx))
    return app, ctx


@pytest.mark.parametrize("operation", ["new", "edit", "delete"])
async def test_every_mutation_rejects_forged_csrf(monkeypatch, operation):
    app, ctx = make_app(monkeypatch)
    suffix = "new" if operation == "new" else f"{uuid4()}/{operation}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/v1/admin/admin/tables/users/{suffix}", data={"csrf_token": "forged"}
        )
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
            "/v1/admin/admin/logout", data={"csrf_token": ctx.csrf_token if valid else "forged"}
        )
    assert response.status_code == (303 if valid else 403)
    assert ctx.auth.logout.await_count == int(valid)


async def test_overview_renders_at_new_admin_path_with_live_summary(monkeypatch):
    app, ctx = make_app(monkeypatch)
    ctx.service = SimpleNamespace(
        overview=AsyncMock(
            return_value=SimpleNamespace(
                order_counts={"NEW": 2, "COMPLETED": 3},
                open_disputes=1,
                pending_withdrawals=4,
                system_balances={"SYSTEM_ESCROW": 100},
                daily_orders=[SimpleNamespace(date=datetime(2026, 9, 30).date(), count=2)],
            )
        ),
        list_orders=AsyncMock(return_value=[]),
        list_audit_logs=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(admin_routes, "_ctx", AsyncMock(return_value=ctx))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"giftly_admin_theme": "dark"},
    ) as client:
        old = await client.get("/admin")
        response = await client.get("/v1/admin/admin")
    assert old.status_code == 404
    assert response.status_code == 200
    assert 'class="sidebar"' in response.text
    assert '<html lang="ar" dir="rtl" data-theme="dark">' in response.text
    assert "أحدث الطلبات" in response.text
    assert "SAR 100.00" in response.text
    assert "الطلبات المنشأة" in response.text
    assert "2026-09-30: 2" in response.text
    assert "حالة الطلبات" in response.text
    assert 'class="daily-bar" width="20" height="100"' in response.text
    assert response.headers["cache-control"] == "no-store"
    ctx.service.list_orders.assert_awaited_once_with(limit=5)
    ctx.service.list_audit_logs.assert_awaited_once_with(limit=5)


async def test_admin_display_preferences_persist_and_reject_unsafe_redirect(monkeypatch):
    app, _ = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preference = await client.get(
            "/v1/admin/admin/preferences",
            params={"lang": "en", "theme": "dark", "next": "https://example.com"},
        )
        page = await client.get("/v1/admin/admin/login")
        invalid = await client.get("/v1/admin/admin/preferences?lang=invalid")
    assert preference.status_code == 303
    assert preference.headers["location"] == "/v1/admin/admin"
    assert 'lang="en" dir="ltr" data-theme="dark"' in page.text
    assert "Admin sign in" in page.text
    assert invalid.status_code == 400


@pytest.fixture
def dashboard_css():
    return Path("app/admin/static/admin.css").read_bytes()


async def test_admin_html_loads_exact_stylesheet_from_content_versioned_path(
    monkeypatch, dashboard_css
):
    app, _ = make_app(monkeypatch)
    css = dashboard_css
    digest = sha256(css).hexdigest()[:16]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/v1/admin/admin/login")
        url = re.search(r'<link rel="stylesheet" href="([^"]+)"', page.text).group(1)
        stylesheet = await client.get(url)
        missing = await client.get("/v1/admin/admin/assets/admin.invalid.css")
    assert url == f"/v1/admin/admin/assets/admin.{digest}.css"
    assert page.headers["cache-control"] == "no-store"
    assert stylesheet.status_code == 200
    assert stylesheet.content == css
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "immutable" in stylesheet.headers["cache-control"]
    assert missing.status_code == 404


async def test_audit_trail_filters_actor_and_pages_without_unbounded_results(monkeypatch):
    app, ctx = make_app(monkeypatch)
    actor_id = uuid4()
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=uuid4(),
            created_at=now,
            actor_user_id=actor_id,
            action="UPDATE",
            entity_type="scheduled_job",
            entity_id=None,
            audit_metadata={"actor_category": "SYSTEM"},
        )
        for _ in range(101)
    ]
    ctx.service = SimpleNamespace(list_audit_logs=AsyncMock(return_value=rows))
    monkeypatch.setattr(admin_routes, "_ctx", AsyncMock(return_value=ctx))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/admin/admin/audit-logs/system")
        invalid = await client.get("/v1/admin/admin/audit-logs/unknown")
    assert response.status_code == 200
    assert response.text.count("تعديل") >= 100
    assert "before_at=" in response.text
    assert 'href="/v1/admin/admin/audit-logs/admin"' in response.text
    assert 'href="/v1/admin/admin/audit-logs/users"' in response.text
    assert 'href="/v1/admin/admin/audit-logs/system"' in response.text
    assert "HTTP_GET" not in response.text
    assert invalid.status_code == 422
    ctx.service.list_audit_logs.assert_awaited_once_with(
        limit=101,
        actor_categories=("SYSTEM",),
        actor_user_id=None,
        action=None,
        entity_type=None,
        before_at=None,
        before_id=None,
    )


async def test_audit_pages_keep_search_filters_and_clear_only_current_view(monkeypatch):
    app, ctx = make_app(monkeypatch)
    ctx.service = SimpleNamespace(list_audit_logs=AsyncMock(return_value=[]))
    monkeypatch.setattr(admin_routes, "_ctx", AsyncMock(return_value=ctx))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/admin/admin/audit-logs/users?action=USER_BAN")
    assert response.status_code == 200
    assert 'href="/v1/admin/admin/audit-logs/admin?action=USER_BAN"' in response.text
    assert 'href="/v1/admin/admin/audit-logs/system?action=USER_BAN"' in response.text
    assert 'href="/v1/admin/admin/audit-logs/users"' in response.text


async def test_authenticated_mutation_does_not_require_password_confirmation(monkeypatch):
    app, ctx = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/admin/tables/users/new", data={"csrf_token": ctx.csrf_token}
        )
    assert response.status_code == 303
    ctx.tables.save.assert_awaited_once()


async def test_step_up_routes_are_removed(monkeypatch):
    app, _ = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        request = await client.post("/v1/admin/admin/step-up/request", data={"next": "/"})
        verify = await client.post("/v1/admin/admin/step-up", data={"password": "example"})
    assert request.status_code == 404
    assert verify.status_code == 404


async def test_authenticated_create_passes_trusted_actor_and_session(monkeypatch):
    app, ctx = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/admin/tables/users/new",
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
            response = await client.get(f"/v1/admin/admin/tables/{table.name}/new")
            assert response.status_code == 200, table.name
            assert response.headers["cache-control"] == "no-store"
            assert "/step-up/request" not in response.text
            assert 'name="null__' not in response.text
            for column in table.c:
                if column.foreign_keys:
                    assert (
                        f'data-url="/v1/admin/admin/relationships/{table.name}/{column.name}"'
                        in response.text
                    )


async def test_edit_form_shows_generated_values_without_writable_inputs(monkeypatch):
    app, ctx = make_app(monkeypatch)
    record_id = uuid4()
    now = datetime(2026, 9, 29, 12, 34, 56, tzinfo=UTC)
    row = {"id": record_id, "created_at": now, "updated_at": now, "deleted_at": now}
    ctx.tables.form = AsyncMock(
        return_value=TableForm(
            "users", form_fields(Base.metadata.tables["users"], row), record_id, "revision"
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/admin/admin/tables/users/{record_id}/edit")

    assert response.status_code == 200
    assert f'value="{record_id}" readonly' in response.text
    assert response.text.count("12:34:56+00:00") == 3
    for name in ("id", "created_at", "updated_at", "deleted_at"):
        assert f'name="{name}"' not in response.text


async def test_datetime_fields_use_picker_and_date_only_fields_keep_date_picker(monkeypatch):
    app, ctx = make_app(monkeypatch)
    starts_at = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    record_id = uuid4()
    ctx.tables.form = AsyncMock(
        return_value=TableForm(
            "promos",
            form_fields(Base.metadata.tables["promos"], {"starts_at": starts_at}),
            record_id,
            "revision",
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        promo = await client.get(f"/v1/admin/admin/tables/promos/{record_id}/edit")
        ctx.tables.form = AsyncMock(
            return_value=TableForm(
                "users", form_fields(Base.metadata.tables["users"], None), None, ""
            )
        )
        user = await client.get("/v1/admin/admin/tables/users/new")

    assert 'type="datetime-local" step="1" data-iso="2026-10-01T12:30:00+00:00"' in promo.text
    assert 'name="starts_at" type="hidden" data-datetime-value' in promo.text
    assert 'name="ends_at" type="hidden" data-datetime-value' in promo.text
    assert 'data-iso="2026-10-01T12:30:00+00:00"' in promo.text
    assert 'name="date_of_birth" type="date"' in user.text
    assert "/static/datetime-fields.js" in promo.text


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
            f"/v1/admin/admin/tables/featured_gifts/{identifier}/edit",
            data={"csrf_token": ctx.csrf_token, "title": "Stale value", "revision": "old-revision"},
        )
    assert response.status_code == 409
    assert "Concurrent change" in response.text
    assert "Stale value" not in response.text


async def test_oversized_form_rejected_before_service_write(monkeypatch):
    app, ctx = make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/admin/admin/tables/users/new",
            content=b"x=" + b"x" * 262_144,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 422
    ctx.tables.save.assert_not_awaited()
