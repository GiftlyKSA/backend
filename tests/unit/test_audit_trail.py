"""Audit entries identify actors without copying request secrets."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app import main
from app.admin.i18n import ARABIC, utc_datetime
from app.core.admin_time import ADMIN_TIMEZONE
from app.core.middleware import RequestIdMiddleware
from app.models import Base
from app.repositories.audit_repository import AuditRepository
from app.routers import auth as auth_routes
from app.schemas.auth import RefreshRequest, RegisterRequest, VerifyOtpRequest
from app.services.auth_service import TokenPair, VerifyResult
from fastapi import FastAPI, HTTPException
from fastapi import Request as FastAPIRequest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from tests.conftest import make_test_settings


async def test_audit_repository_tags_actor_and_supports_stable_filters() -> None:
    session = SimpleNamespace(info={"audit_actor_category": "ADMIN"})
    session.add = Mock()
    session.flush = AsyncMock()
    session.scalars = AsyncMock(return_value=[])
    repository = AuditRepository(session)
    actor_id = uuid4()
    row = await repository.record(
        actor_user_id=actor_id,
        action="USER_BAN",
        entity_type="users",
        entity_id=actor_id,
        metadata={"fields": ["status"]},
    )
    assert row.audit_metadata == {"fields": ["status"], "actor_category": "ADMIN"}
    assert session.info["committed_audit_events"] == [row]

    before_at = datetime(2026, 9, 30, tzinfo=UTC)
    await repository.list_recent(
        actor_category="ADMIN", before_at=before_at, before_id=actor_id, limit=101
    )
    query = session.scalars.call_args.args[0]
    compiled_query = query.compile(dialect=postgresql.dialect())
    compiled = str(compiled_query)
    assert "->> 'actor_category'" in compiled
    assert "audit_logs.created_at" in compiled
    assert "audit_logs.id" in compiled
    assert "LIMIT" in compiled

    await repository.list_recent(actor_categories=("USER", "CUSTOMER", "COURIER"))
    grouped = str(
        session.scalars.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert " IN " in grouped
    assert "HTTP" in grouped

    await repository.list_recent(
        activity_id=actor_id,
        activity_name="UPDATE_%",
        action="UPDATE",
        entity_type="users",
        start_at=before_at,
        end_at=before_at,
        before_at=before_at,
        before_id=actor_id,
        oldest_first=True,
        limit=26,
    )
    statement = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(statement)
    assert "audit_logs.created_at ASC, audit_logs.id ASC" in sql
    assert "(audit_logs.created_at, audit_logs.id) >" in sql
    assert "audit_logs.created_at >=" in sql
    assert "audit_logs.created_at <=" in sql
    assert "ILIKE" in sql
    assert "%UPDATE\\_\\%%" in statement.params.values()
    assert 26 in statement.params.values()


async def test_audit_actor_lookup_is_batched_and_selects_only_display_fields():
    actor_id = uuid4()
    session = SimpleNamespace(
        execute=AsyncMock(return_value=[(actor_id, "Full Name", "name@example.com")])
    )
    repository = AuditRepository(session)
    assert await repository.list_actors(set()) == {}
    actors = await repository.list_actors({actor_id})
    assert actors[actor_id].full_name == "Full Name"
    assert actors[actor_id].email == "name@example.com"
    session.execute.assert_awaited_once()
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "users.full_name" in sql and "users.email" in sql
    assert "users.phone" not in sql


def test_activity_timestamp_is_explicitly_normalized_to_utc():
    value = datetime.fromisoformat("2026-10-01T15:30:00+03:00")
    assert utc_datetime(value) == "2026-10-01 12:30:00 UTC"


async def test_audit_dropdown_choices_are_scoped_bounded_and_exclude_request_logs():
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[["CREATE", "UPDATE"], ["users"]]))
    choices = await AuditRepository(session).filter_choices(("SYSTEM",))
    assert choices.actions == ["CREATE", "UPDATE"]
    assert choices.entities == ["users"]
    assert session.scalars.await_count == 2
    for call in session.scalars.call_args_list:
        compiled = call.args[0].compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "DISTINCT" in sql and "LIMIT" in sql
        assert "actor_category" in sql
        assert 200 in compiled.params.values()
        assert ["SYSTEM"] in compiled.params.values()
        assert "HTTP\\_%" in compiled.params.values()


async def test_riyadh_audit_date_bounds_are_bound_as_utc():
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]))
    start = datetime(2026, 10, 1, 15, tzinfo=ADMIN_TIMEZONE)
    end = datetime(2026, 10, 1, 16, tzinfo=ADMIN_TIMEZONE)
    await AuditRepository(session).list_recent(start_at=start, end_at=end)
    compiled = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    dates = [value for value in compiled.params.values() if isinstance(value, datetime)]
    assert dates == [datetime(2026, 10, 1, 12, tzinfo=UTC), datetime(2026, 10, 1, 13, tzinfo=UTC)]
    assert all(value.tzinfo is UTC for value in dates)


async def test_http_requests_do_not_create_database_audit_rows() -> None:
    factory_calls = 0

    class SessionContext:
        def __init__(self):
            nonlocal factory_calls
            factory_calls += 1

        async def __aenter__(self):
            return SimpleNamespace(info={}, commit=AsyncMock())

        async def __aexit__(self, *_args):
            return None

    app = FastAPI()
    app.state.session_factory = SessionContext
    main._install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=False))

    @app.get("/api/items/{item_id}")
    async def read_item(request: FastAPIRequest, item_id: str) -> dict[str, str]:
        request.state.audit_actor_id = uuid4()
        request.state.audit_actor_category = "USER"
        return {"id": item_id}

    @app.post("/api/items/{item_id}")
    async def reject_item(item_id: str) -> None:
        raise HTTPException(status_code=403)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        read = await client.get("/api/items/private-id?token=secret")
        denied = await client.post("/api/items/private-id", content=b"secret")
        health = await client.get("/api/health")

    assert (read.status_code, denied.status_code, health.status_code) == (200, 403, 404)
    assert factory_calls == 0


@pytest.mark.parametrize("operation", ["verify", "register", "refresh"])
async def test_successful_auth_operation_is_attributed_to_issued_user(
    monkeypatch, operation
) -> None:
    user_id = uuid4()
    tokens = TokenPair(
        access_token="secret-access",
        refresh_token="secret-refresh",
        role="CUSTOMER",
        user_id=user_id,
    )
    service = SimpleNamespace(
        verify_otp=AsyncMock(return_value=VerifyResult(False, tokens, None)),
        register=AsyncMock(return_value=tokens),
        refresh=AsyncMock(return_value=tokens),
    )
    monkeypatch.setattr(auth_routes, "_service", lambda *_args: service)
    request = Request({"type": "http", "app": SimpleNamespace()})

    if operation == "verify":
        await auth_routes.verify_otp(
            request, None, VerifyOtpRequest(phone="0501234567", otp="123456")
        )
    elif operation == "register":
        await auth_routes.register(
            request,
            None,
            RegisterRequest(registration_token="secret-registration", role="CUSTOMER"),
        )
    else:
        await auth_routes.refresh(request, None, RefreshRequest(refresh_token="secret-refresh"))

    assert request.state.audit_actor_id == user_id
    assert request.state.audit_actor_category == "CUSTOMER"


async def test_untrusted_request_id_is_bounded_before_logging() -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/")
    async def home() -> dict[str, bool]:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unsafe = await client.get("/", headers={"X-Request-ID": "secret=" + "x" * 200})
        safe = await client.get("/", headers={"X-Request-ID": "client-123"})
    assert len(unsafe.headers["X-Request-ID"]) == 36
    assert safe.headers["X-Request-ID"] == "client-123"


def test_arabic_catalog_covers_every_admin_table_and_field() -> None:
    for table in Base.metadata.tables.values():
        assert table.name.replace("_", " ").title() in ARABIC
        for column in table.c:
            label = column.name.removesuffix("_encrypted").replace("_", " ").title()
            assert label in ARABIC, (table.name, column.name)
