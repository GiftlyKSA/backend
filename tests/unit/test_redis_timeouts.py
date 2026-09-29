"""Redis clients bound ordinary commands without interrupting the worker queue read."""

from __future__ import annotations

import pytest
from app.core.redis import (
    REDIS_CONNECT_TIMEOUT_SECONDS,
    REDIS_MAX_CONNECTIONS,
    REDIS_OPERATION_TIMEOUT_SECONDS,
    build_redis,
)
from redis.exceptions import TimeoutError as RedisTimeoutError

from tests.conftest import make_test_settings


@pytest.mark.asyncio
async def test_application_redis_enforces_timeout_over_url_options() -> None:
    settings = make_test_settings(
        REDIS_URL="redis://localhost:6379/0?socket_timeout=60&socket_connect_timeout=60"
    )
    client = build_redis(settings)
    try:
        pool = client.connection_pool
        assert pool.max_connections == REDIS_MAX_CONNECTIONS
        assert pool.connection_kwargs["socket_timeout"] == REDIS_OPERATION_TIMEOUT_SECONDS
        assert pool.connection_kwargs["socket_connect_timeout"] == REDIS_CONNECT_TIMEOUT_SECONDS
        assert pool.connection_kwargs["retry_on_timeout"] is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stalled_redis_command_times_out_and_connection_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    connections_closed = asyncio.Event()

    async def stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            connections_closed.set()

    server = await asyncio.start_server(stall, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr("app.core.redis.REDIS_OPERATION_TIMEOUT_SECONDS", 0.1)
    client = build_redis(make_test_settings(REDIS_URL=f"redis://127.0.0.1:{port}/0"))
    try:
        with pytest.raises(RedisTimeoutError):
            await asyncio.wait_for(client.ping(), timeout=1)
        await asyncio.wait_for(connections_closed.wait(), timeout=1)
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()
