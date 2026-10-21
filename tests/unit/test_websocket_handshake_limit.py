from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core import ws_connections
from app.core.jwt import create_access_token
from app.core.ratelimit import RateLimitDecision
from app.routers import chat, order_events

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_handshake_limit_is_account_scoped_and_uses_bounded_defaults(monkeypatch) -> None:
    account_id = uuid4()
    decision = RateLimitDecision(allowed=True, retry_after_seconds=0)
    constructor = {}

    class RecordingLimiter:
        def __init__(self, redis, *, max_requests: int, window_seconds: int) -> None:
            constructor.update(max_requests=max_requests, window_seconds=window_seconds)

        async def check(self, identity: str) -> RateLimitDecision:
            constructor["identity"] = identity
            return decision

    monkeypatch.setattr(ws_connections, "RateLimiter", RecordingLimiter)
    settings = make_test_settings()

    result = await ws_connections.check_websocket_handshake(AsyncMock(), account_id, settings)

    assert result is decision
    assert constructor == {
        "max_requests": 30,
        "window_seconds": 60,
        "identity": f"ws-handshake:{account_id}",
    }


def test_uvicorn_websocket_transport_has_bounded_buffers_and_no_compression() -> None:
    from app.core.uvicorn_settings import UVICORN_WORKER_CONFIG
    from uvicorn import Config

    config = Config("app.main:app", **UVICORN_WORKER_CONFIG)

    assert config.ws == "websockets-sansio"
    assert config.ws_max_size == 4096
    assert config.ws_max_queue == 1
    assert config.ws_per_message_deflate is False


@pytest.mark.asyncio
async def test_chat_handshake_limit_rejects_before_database_access(monkeypatch) -> None:
    settings = make_test_settings()
    account_id = uuid4()
    token, _, _ = create_access_token(settings, user_id=account_id, role="CUSTOMER")
    session_factory = AsyncMock()
    websocket = SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        query_params={},
        scope={"subprotocols": []},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                redis=AsyncMock(),
                session_factory=session_factory,
            )
        ),
        close=AsyncMock(),
    )
    denied = RateLimitDecision(allowed=False, retry_after_seconds=20)
    monkeypatch.setattr(chat, "check_websocket_handshake", AsyncMock(return_value=denied))

    await chat.conversation_ws(websocket, uuid4())

    websocket.close.assert_awaited_once_with(code=4429)
    session_factory.assert_not_called()


def test_order_socket_does_not_fallback_from_invalid_header_to_protocol() -> None:
    websocket = SimpleNamespace(
        headers={"authorization": "Basic invalid"},
        scope={"subprotocols": ["bearer.sensitive-value"]},
    )

    assert order_events._token(websocket) == ""
