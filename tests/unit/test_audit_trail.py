"""Audit entries identify actors without copying request secrets."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app import main
from app.admin.i18n import ARABIC
from app.core.middleware import RequestIdMiddleware
from app.models import Base
from app.repositories.audit_repository import AuditRepository
from app.routers import auth as auth_routes
from app.schemas.auth import RefreshRequest, RegisterRequest, VerifyOtpRequest
from app.services.auth_service import TokenPair, VerifyResult
from app.workers import audit as worker_audit
from fastapi import FastAPI, HTTPException
from fastapi import Request as FastAPIRequest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql
from starlette.requests import Request


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


async def test_http_audit_records_route_template_without_query_or_body(monkeypatch) -> None:
    actor_id = uuid4()
    session = SimpleNamespace(info={}, commit=AsyncMock())

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return None

    captured = AsyncMock()
    monkeypatch.setattr(main, "AuditRepository", lambda _session: SimpleNamespace(record=captured))
    monkeypatch.setattr(main, "emit_committed_audit_events", lambda _session: None)
    app = SimpleNamespace(state=SimpleNamespace(session_factory=SessionContext))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/users/private-id",
            "query_string": b"token=private-value",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "route": SimpleNamespace(path="/api/users/{user_id}"),
            "app": app,
        }
    )
    request.state.audit_actor_id = actor_id
    request.state.audit_actor_category = "USER"

    await main._record_http_audit(request, 200)

    saved = captured.call_args.kwargs
    assert saved["actor_user_id"] == actor_id
    assert saved["metadata"]["route"] == "/api/users/{user_id}"
    assert saved["metadata"]["status"] == 200
    assert "private-value" not in str(saved)
    assert "private-id" not in str(saved)
    assert session.info["audit_actor_category"] == "USER"
    session.commit.assert_awaited_once()


async def test_http_audit_middleware_records_reads_and_rejections(monkeypatch) -> None:
    captured = AsyncMock()
    monkeypatch.setattr(main, "AuditRepository", lambda _session: SimpleNamespace(record=captured))
    monkeypatch.setattr(main, "emit_committed_audit_events", lambda _session: None)

    class SessionContext:
        async def __aenter__(self):
            return SimpleNamespace(info={}, commit=AsyncMock())

        async def __aexit__(self, *_args):
            return None

    app = FastAPI()
    app.state.session_factory = SessionContext
    main._install_audit(app)

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
    assert captured.await_count == 2
    read_record, denied_record = [call.kwargs for call in captured.await_args_list]
    assert read_record["action"] == "HTTP_GET"
    assert read_record["metadata"]["route"] == "/api/items/{item_id}"
    assert denied_record["action"] == "HTTP_POST"
    assert denied_record["metadata"]["status"] == 403
    assert "private-id" not in str(captured.await_args_list)
    assert "secret" not in str(captured.await_args_list)


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
    assert request.state.audit_actor_category == "USER"


async def test_scheduled_job_outcome_is_recorded_on_failure(monkeypatch) -> None:
    record = AsyncMock()
    monkeypatch.setattr(worker_audit, "_record_job_outcome", record)

    @worker_audit.audited_system_job("example_job")
    async def failing_job() -> None:
        raise RuntimeError("job failed")

    with pytest.raises(RuntimeError, match="job failed"):
        await failing_job()
    assert [call.args for call in record.await_args_list] == [
        ("example_job", "STARTED"),
        ("example_job", "FAILED"),
    ]


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
