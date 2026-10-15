"""Transactional cache epochs and bounded mixed-TTL Redis storage."""

import os
from uuid import uuid4

import pytest
from app.services.result_cache import _STORE
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text

_VERSION = text(
    "SELECT version FROM read_cache_versions WHERE scope='catalog' "
    "AND owner_id='00000000-0000-0000-0000-000000000000'"
)


async def test_catalog_epoch_changes_are_visible_only_after_commit(db_connection):
    identifier = uuid4()
    engine = db_connection.engine
    async with engine.connect() as reader:
        old = await reader.scalar(_VERSION)
        try:
            async with engine.begin() as writer:
                await writer.execute(
                    text(
                        "INSERT INTO cities(id,name,name_ar,shortcut) "
                        "VALUES(CAST(:id AS uuid),:name,:name,:shortcut)"
                    ),
                    {
                        "id": identifier,
                        "name": f"Cache fixture {identifier}",
                        "shortcut": identifier.hex[:12],
                    },
                )
                assert await writer.scalar(_VERSION) != old
                assert await reader.scalar(_VERSION) == old
            committed = await reader.scalar(_VERSION)
            assert committed != old
        finally:
            async with engine.begin() as writer:
                await writer.execute(
                    text("DELETE FROM cities WHERE id=CAST(:id AS uuid)"), {"id": identifier}
                )


async def test_read_cache_global_capacity_and_index_survive_short_ttl_writes():
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15"))
    index = f"test:read-cap:{uuid4()}"
    keys = [f"{index}:{i}" for i in range(3)]
    try:
        try:
            await redis.ping()
        except RedisError:
            pytest.skip("Disposable Redis unavailable.")
        for key, ttl in zip(keys, [3600, 3600, 30], strict=True):
            await redis.eval(_STORE, 2, index, key, b"[]", ttl, 2)
        assert await redis.zcard(index) == 2
        assert sum([bool(await redis.exists(key)) for key in keys]) == 2
        assert await redis.ttl(index) > 3600
        assert 0 < await redis.ttl(keys[1]) <= 3600
    finally:
        await redis.delete(index, *keys)
        await redis.aclose()
