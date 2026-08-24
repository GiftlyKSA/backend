"""Availability classifiers shared by integration-test fixtures."""

from __future__ import annotations

import errno
import socket

import pytest
from redis.exceptions import (
    AuthenticationError,
)
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

_CONNECTION_ERRNOS = frozenset(
    value
    for name in (
        "ECONNREFUSED",
        "ECONNRESET",
        "ECONNABORTED",
        "ETIMEDOUT",
        "ENETUNREACH",
        "EHOSTUNREACH",
    )
    if (value := getattr(errno, name, None)) is not None
)
_CONNECTION_WINERRORS = frozenset({10051, 10053, 10054, 10060, 10061, 10065, 11001, 1225})


def raise_or_skip_redis_unavailable(exc: Exception) -> None:
    """Skip only recognized Redis connection failures; re-raise every other error."""
    if isinstance(exc, AuthenticationError):
        raise exc
    if isinstance(exc, (RedisConnectionError, RedisTimeoutError)):
        pytest.skip(f"redis unavailable: {exc}")
    if isinstance(exc, OSError) and (
        isinstance(exc, (ConnectionRefusedError, TimeoutError, socket.gaierror))
        or exc.errno in _CONNECTION_ERRNOS
        or getattr(exc, "winerror", None) in _CONNECTION_WINERRORS
        or exc.errno in _CONNECTION_WINERRORS
    ):
        pytest.skip(f"redis unavailable: {exc}")
    raise exc
