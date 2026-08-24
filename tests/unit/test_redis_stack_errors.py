"""Classification tests for Redis integration probes."""

from __future__ import annotations

import errno

import pytest
from redis.exceptions import (
    AuthenticationError,
    DataError,
    InvalidResponse,
    ResponseError,
)
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

from tests.integration.availability import raise_or_skip_redis_unavailable


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(errno.ECONNREFUSED, "connection refused"),
        TimeoutError(errno.ETIMEDOUT, "connection timed out"),
        RedisConnectionError("connection reset by peer"),
        RedisTimeoutError("timeout reading from socket"),
    ],
)
def test_redis_probe_skips_recognized_connectivity_failures(error: Exception) -> None:
    """Transport failures and Redis connection exceptions make the service unavailable."""
    with pytest.raises(pytest.skip.Exception):
        raise_or_skip_redis_unavailable(error)


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("invalid username-password pair"),
        ResponseError("unknown command"),
        InvalidResponse("protocol parse failure"),
        DataError("invalid client argument"),
        RuntimeError("fixture programming error"),
    ],
)
def test_redis_probe_reraises_auth_protocol_driver_and_programming_errors(
    error: Exception,
) -> None:
    """Non-connectivity failures must remain visible test failures."""
    try:
        raise_or_skip_redis_unavailable(error)
    except pytest.skip.Exception as exc:
        pytest.fail(f"non-connectivity Redis failure was incorrectly skipped: {exc}")
    except type(error) as caught:
        assert caught is error
    else:  # pragma: no cover - the helper must always raise or skip
        pytest.fail("Redis probe error was swallowed")
