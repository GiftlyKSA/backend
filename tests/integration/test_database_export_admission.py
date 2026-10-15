"""Retained export admission is shared and survives the route returning."""

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.admin import backup_router
from app.core.exceptions import ConflictError
from app.core.locks import redis_lock
from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from tests.conftest import make_test_settings


async def test_completed_exports_share_retained_admission_and_cleanup(monkeypatch, tmp_path):
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15"))
    namespace = f"test:export:{uuid4()}:"
    try:
        try:
            await redis.ping()
        except RedisError:
            pytest.skip("Disposable Redis is unavailable.")

        @asynccontextmanager
        async def isolated_lock(client, key, **kwargs):
            async with redis_lock(client, namespace + key, **kwargs) as token:
                yield token

        monkeypatch.setattr(backup_router, "redis_lock", isolated_lock)
        monkeypatch.setattr(backup_router, "get_redis_from", lambda request: redis)
        monkeypatch.setattr(
            backup_router, "get_settings_from", lambda request: make_test_settings()
        )
        monkeypatch.setattr(backup_router, "require_admin", AsyncMock(return_value=object()))
        monkeypatch.setattr(backup_router, "verify_csrf", lambda *args: None)
        paths = []

        async def create(settings, password):
            directory = tmp_path / str(len(paths))
            directory.mkdir()
            path = directory / "giftly.sql"
            path.write_bytes(b"private snapshot")
            paths.append(path)
            return path

        monkeypatch.setattr(backup_router, "create_database_export", create)
        request = Request({"type": "http", "method": "POST", "headers": []})
        db = SimpleNamespace(commit=AsyncMock())
        first = await backup_router.export_database(request, db, "csrf", "plain")
        assert await redis.exists(namespace + "lock:database_export_download")
        assert not await redis.exists(namespace + "lock:database_export")
        with pytest.raises(ConflictError):
            await backup_router.export_database(request, db, "csrf", "plain")
        assert paths[0].exists() and not paths[1].parent.exists()
        assert await redis.exists(namespace + "lock:database_export_download")

        async def disconnected(message):
            raise OSError("disconnected")

        with pytest.raises(OSError):
            await first(request.scope, AsyncMock(), disconnected)
        assert not paths[0].parent.exists()
        assert not await redis.exists(namespace + "lock:database_export_download")
        third = await backup_router.export_database(request, db, "csrf", "plain")
        messages = []

        async def send(message):
            messages.append(message)

        scope = {**request.scope, "extensions": {"http.response.pathsend": {}}}
        await third(scope, AsyncMock(), send)
        assert not any(message["type"] == "http.response.pathsend" for message in messages)
        assert b"".join(m.get("body", b"") for m in messages) == b"private snapshot"
        assert not paths[2].parent.exists()
        assert not await redis.exists(namespace + "lock:database_export_download")
    finally:
        await redis.delete(
            namespace + "lock:database_export", namespace + "lock:database_export_download"
        )
        await redis.aclose()
