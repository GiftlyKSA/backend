"""Actual body boundaries and safe shared-throttle outages."""

import asyncio
import json
import logging
from itertools import count
from unittest.mock import AsyncMock, Mock

import pytest
from app import main
from app.core import middleware, ratelimit
from app.core.middleware import BodySizeMiddleware
from app.main import _close_shared_resources, _install_middleware
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from tests.conftest import make_test_settings


async def _raw_request(app, chunks, headers, *, receive_override=None):
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
        receive if receive_override is None else receive_override,
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


@pytest.mark.parametrize("trickle", [False, True])
async def test_incomplete_body_times_out_before_invoking_handler(trickle):
    handler = AsyncMock()
    app = BodySizeMiddleware(handler, max_bytes=1024, idle_seconds=0.02, total_seconds=0.05)

    async def receive():
        if trickle:
            await asyncio.sleep(0.001)
            return {"type": "http.request", "body": b"x", "more_body": True}
        await asyncio.Event().wait()

    async with asyncio.timeout(1):
        start, body = await _raw_request(app, [], [], receive_override=receive)
    assert start["status"] == 408
    assert json.loads(body)["error"]["code"] == "REQUEST_BODY_TIMEOUT"
    handler.assert_not_awaited()


async def test_resource_cleanup_continues_after_provider_failure():
    app = FastAPI()
    app.state.clients = AsyncMock()
    app.state.clients.gateway.aclose.side_effect = RuntimeError("provider close failed")
    app.state.subscription_redis = AsyncMock()
    app.state.redis = AsyncMock()
    app.state.engine = AsyncMock()

    await _close_shared_resources(app)

    app.state.clients.storage.aclose.assert_awaited_once()
    app.state.subscription_redis.aclose.assert_awaited_once()
    app.state.redis.aclose.assert_awaited_once()
    app.state.engine.dispose.assert_awaited_once()


async def test_partial_startup_releases_resources_before_propagating_error(monkeypatch):
    clients = AsyncMock()
    engine = AsyncMock()
    redis = AsyncMock()
    monkeypatch.setattr(main, "get_settings", make_test_settings)

    def build_clients(settings, *, register):
        register("storage", clients.storage)
        return clients

    monkeypatch.setattr(main, "build_clients", build_clients)
    monkeypatch.setattr(main, "build_engine", Mock(return_value=engine))
    monkeypatch.setattr(main, "build_session_factory", Mock())
    monkeypatch.setattr(main, "build_redis", Mock(return_value=redis))
    monkeypatch.setattr(main, "build_subscription_redis", Mock(side_effect=RuntimeError("startup")))

    app = main.create_app()
    with pytest.raises(RuntimeError, match="startup"):
        async with app.router.lifespan_context(app):
            pytest.fail("A failed startup must not serve requests.")
    clients.storage.aclose.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()


async def test_rate_limit_outage_reports_first_failure_and_periodic_count(monkeypatch, caplog):
    redis = AsyncMock()
    redis.eval.side_effect = ConnectionError("private backend details")
    limiter = ratelimit.RateLimiter(redis, max_requests=1, window_seconds=60, scope="test-outage")
    now = 1000.0
    monkeypatch.setattr(ratelimit, "perf_counter", lambda: now)

    for _ in range(100):
        assert (await limiter.check("synthetic")).unavailable
    warnings = [record for record in caplog.records if record.name == "app.ratelimit"]
    assert len(warnings) == 1
    assert warnings[0].extra_fields["failures"] == 1

    now += 60
    assert (await limiter.check("synthetic")).unavailable
    warnings = [record for record in caplog.records if record.name == "app.ratelimit"]
    assert len(warnings) == 2
    assert warnings[1].extra_fields["failures"] == 100
    assert "private backend details" not in caplog.text


async def test_request_timing_samples_only_route_templates(monkeypatch, caplog):
    app = FastAPI()
    _install_middleware(app, make_test_settings())
    monkeypatch.setattr(middleware, "_request_samples", count())
    monkeypatch.setattr(middleware, "_REQUEST_SAMPLE_EVERY", 4)

    @app.get("/api/example/{resource_id}")
    async def example(resource_id: str):
        return {"ok": True}

    with caplog.at_level(logging.INFO, logger="app.request"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(8):
                assert (await client.get("/api/example/private-value")).status_code == 200
    samples = [record for record in caplog.records if record.message == "request_timing"]
    assert len(samples) == 2
    assert all(record.extra_fields["route"] == "/api/example/{resource_id}" for record in samples)
    assert all(record.extra_fields["status"] == 200 for record in samples)
    assert all("private-value" not in str(record.extra_fields) for record in samples)


@pytest.mark.parametrize("path", ["/api/example", "/api/auth/send-otp", "/api/admin-extra/users"])
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
        assert (await client.get("/api/health")).status_code == 404
        assert (await client.get("/api/health/ready")).status_code == 404
        assert (await client.get("/api/health/live")).status_code == 503
        assert (await client.post("/api/health")).status_code == 503
        assert (await client.options(path)).status_code == 503
        preflight = await client.options(
            path,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 200
    assert visits == []
