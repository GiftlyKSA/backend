"""Classification tests for the mobile integration database probe."""

from __future__ import annotations

import errno

import pytest
from sqlalchemy.exc import OperationalError

from tests.integration.test_mobile_profile_contracts import (
    _raise_or_skip_database_unavailable,
)


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(errno.ECONNREFUSED, "connection refused"),
        TimeoutError(errno.ETIMEDOUT, "connection timed out"),
        OperationalError(
            "connect", {}, ConnectionRefusedError(errno.ECONNREFUSED, "connection refused")
        ),
        OperationalError("connect", {}, _DriverError("08006")),
    ],
)
def test_database_probe_skips_only_recognized_connectivity_failures(error: Exception) -> None:
    """Known transport and connection SQLSTATE failures make DB-backed tests unavailable."""
    with pytest.raises(pytest.skip.Exception):
        _raise_or_skip_database_unavailable(error)


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.EACCES, "permission denied"),
        OperationalError("SELECT broken", {}, _DriverError("42601")),
        OperationalError("SELECT broken", {}, RuntimeError("driver invariant failed")),
    ],
)
def test_database_probe_reraises_non_connectivity_operational_errors(error: Exception) -> None:
    """Schema, query, driver, and unrelated OS failures must remain test failures."""
    try:
        _raise_or_skip_database_unavailable(error)
    except pytest.skip.Exception as exc:
        pytest.fail(f"non-connectivity failure was incorrectly skipped: {exc}")
    except type(error) as caught:
        assert caught is error
    else:  # pragma: no cover - the helper must always raise or skip
        pytest.fail("database probe error was swallowed")
