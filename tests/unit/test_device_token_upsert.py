"""Device registration must be one atomic database write."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.models.enums import DeviceOs
from app.repositories.device_token_repository import DeviceTokenRepository
from sqlalchemy.dialects import postgresql


class _Result:
    def scalar_one(self) -> Any:
        return object()


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result()

    async def flush(self) -> None:
        pass

    async def scalar(self, statement: Any) -> Any:
        raise AssertionError("registration must not select before writing")


@pytest.mark.asyncio
async def test_registration_atomically_reassigns_unique_token() -> None:
    session = _Session()
    await DeviceTokenRepository(session).register(  # type: ignore[arg-type]
        user_id=uuid.uuid4(), token="token", device_os=DeviceOs.IOS
    )

    assert len(session.statements) == 1
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT uq_device_tokens_token DO UPDATE" in sql
    assert "user_id = excluded.user_id" in sql
    assert "device_os = excluded.device_os" in sql
    assert "RETURNING" in sql
