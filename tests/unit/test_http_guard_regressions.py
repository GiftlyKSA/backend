"""Actual body boundaries and safe shared-throttle outages."""

import json
from unittest.mock import AsyncMock

import pytest
from app.main import _install_middleware
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings


async def _raw_request(app, chunks, headers):
    pending = list(chunks)
    messages = []

    async def receive():
        if pending:
            return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/example",
            "raw_path": b"/api/example",
            "query_string": b"",
            "headers": [(b"x-request-id", b"body-regression"), *headers],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages)
    return start, body


@pytest.mark.parametrize("length", [None, b"garbage", b"-1", b"1", b"8"])
@pytest.mark.parametrize("chunks", [[b"123456789"], [b"1234", b"5678", b"9"]])
async def test_actual_body_limit_rejects_before_route_side_effect(length, chunks):
    app = FastAPI()
    _install_middleware(app, make_test_settings(MAX_REQUEST_BODY_BYTES=8))
    visits = []

    @app.post("/api/example")
    async def example():
        visits.append(True)
        return {"ok": True}

    headers = [] if length is None else [(b"content-length", length)]
    start, body = await _raw_request(app, chunks, headers)
    assert start["status"] == 413
    assert visits == []
    assert json.loads(body)["error"] == {
        "code": "PAYLOAD_TOO_LARGE",
        "message": "The request body is too large.",
        "request_id": "body-regression",
    }
    assert dict(start["headers"])[b"x-content-type-options"] == b"nosniff"


async def test_exact_limit_body_is_replayed_to_parser():
    app = FastAPI()
    _install_middleware(app, make_test_settings(MAX_REQUEST_BODY_BYTES=8))

    @app.post("/api/example")
    async def example(request: Request):
        return {"body": (await request.body()).decode()}

    start, body = await _raw_request(app, [b"1234", b"5678"], [])
    assert start["status"] == 200
    assert json.loads(body) == {"body": "12345678"}


@pytest.mark.parametrize("path", ["/api/example", "/api/auth/send-otp", "/api/admin/users"])
async def test_redis_outage_returns_safe_503_before_route(path):
    app = FastAPI()
    app.state.redis = AsyncMock()
    app.state.redis.eval.side_effect = ConnectionError("backend down")
    _install_middleware(app, make_test_settings(RATE_LIMIT_ENABLED=True))
    visits = []

    @app.get(path)
    async def example():
        visits.append(True)
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path, headers={"X-Request-ID": "outage-regression"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"
        assert response.json()["error"]["request_id"] == "outage-regression"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert (await client.get("/api/health/live")).status_code == 404
        assert (await client.options(path)).status_code == 405
    assert visits == []
