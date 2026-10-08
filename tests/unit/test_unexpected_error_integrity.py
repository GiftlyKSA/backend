"""Unexpected failures keep correlation and protective headers without disclosing data."""

import asyncio
import logging

import pytest
from app.core.middleware import RequestIdMiddleware, current_request_id, register_exception_handlers
from app.main import _install_middleware
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings


@pytest.mark.parametrize("failure", ["route", "guard"])
async def test_unexpected_failure_preserves_safe_envelope_and_headers(monkeypatch, caplog, failure):
    app = FastAPI()
    _install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=failure == "guard"))
    register_exception_handlers(app)
    sensitive = "private@example.com secret-value-that-must-not-be-logged"

    async def explode(*args, **kwargs):
        raise RuntimeError(sensitive)

    if failure == "guard":
        app.state.redis = object()
        monkeypatch.setattr("app.main.RateLimiter.check", explode)

    async def route():
        raise RuntimeError(sensitive)

    app.get("/failure")(route)
    with caplog.at_level(logging.ERROR, logger="app.request"):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.get(
                "/failure",
                headers={"X-Request-ID": "failure-123", "Origin": "http://localhost:3000"},
            )
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "request_id": "failure-123",
        }
    }
    assert response.headers["X-Request-ID"] == "failure-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Strict-Transport-Security"]
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "server" not in response.headers
    assert sensitive not in response.text
    assert sensitive not in caplog.text
    failures = [record for record in caplog.records if record.name == "app.request"]
    assert len(failures) == 1
    assert failures[0].request_id == "failure-123"
    assert failures[0].exc_info is None
    assert current_request_id() == "-"


@pytest.mark.parametrize("failure", [RuntimeError("stream failed"), asyncio.CancelledError()])
async def test_started_response_or_cancellation_is_not_replaced_with_500(failure):
    sent = []

    async def downstream(scope, receive, send):
        assert scope["state"]["request_id"] == "stream-123"
        if isinstance(failure, RuntimeError):
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise failure

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": [(b"x-request-id", b"stream-123")], "method": "GET"}
    with pytest.raises(type(failure)):
        await RequestIdMiddleware(downstream)(scope, receive, send)
    assert len(sent) == (1 if isinstance(failure, RuntimeError) else 0)
    if sent:
        assert sent[0]["status"] == 200
        assert (b"x-request-id", b"stream-123") in sent[0]["headers"]
    assert current_request_id() == "-"
