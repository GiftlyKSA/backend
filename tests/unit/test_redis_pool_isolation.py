"""Subscriptions must never exhaust the HTTP/security connection budget."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core import redis as factory
from redis.exceptions import MaxConnectionsError

from tests.conftest import make_test_settings


async def test_subscription_exhaustion_leaves_http_pool_available():
    settings = make_test_settings(REDIS_URL="redis://127.0.0.1:1/0?max_connections=9999")
    http = factory.build_redis(settings)
    subscriptions = factory.build_subscription_redis(settings)
    try:
        assert http.connection_pool is not subscriptions.connection_pool
        assert http.connection_pool.max_connections == factory.REDIS_MAX_CONNECTIONS
        assert (
            subscriptions.connection_pool.max_connections
            == factory.REDIS_SUBSCRIPTION_MAX_CONNECTIONS
        )
        assert subscriptions.connection_pool.connection_kwargs["socket_timeout"] == 3
        assert http.connection_pool.connection_kwargs["socket_timeout"] == 3
        for _ in range(factory.REDIS_SUBSCRIPTION_MAX_CONNECTIONS):
            subscriptions.connection_pool.get_available_connection()
        with pytest.raises(MaxConnectionsError):
            subscriptions.connection_pool.get_available_connection()
        assert http.connection_pool.can_get_connection()
        http.connection_pool.get_available_connection()
    finally:
        await subscriptions.aclose()
        await http.aclose()


async def test_both_clients_own_and_close_their_pools():
    settings = make_test_settings()
    for build in (factory.build_redis, factory.build_subscription_redis):
        client = build(settings)
        client.connection_pool.disconnect = AsyncMock()
        await client.aclose()
        client.connection_pool.disconnect.assert_awaited_once()


async def test_idle_pubsub_read_does_not_remove_handshake_deadlines():
    client = factory.build_subscription_redis(make_test_settings())
    pubsub = client.pubsub()
    connection = SimpleNamespace(
        is_connected=True, read_response=AsyncMock(), health_check_interval=0
    )
    pubsub.connection = connection
    pubsub.check_health = AsyncMock()
    pubsub._execute = AsyncMock(return_value=None)
    try:
        await pubsub.parse_response(block=True)
        assert pubsub._execute.await_args.kwargs["timeout"] == math.inf
        assert client.connection_pool.connection_kwargs["socket_timeout"] == 3
    finally:
        pubsub.connection = None
        await pubsub.aclose()
        await client.aclose()
