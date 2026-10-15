"""Read-through cache budgets, isolation and failure behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.services.result_cache import ResultCache
from pydantic import TypeAdapter
from redis.exceptions import ConnectionError


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = []

    async def getrange(self, key, start, end):
        return self.values.get(key, b"")[start : end + 1]

    async def eval(self, script, count, index, key, value, ttl, maximum):
        self.values[key] = value
        self.ttls.append(ttl)
        return 1


def arguments(repository, load, **overrides):
    values = dict(
        repository=repository,
        domain="catalog",
        owner=None,
        role="public",
        auth_version=0,
        filters={"view": "cities"},
        adapter=TypeAdapter(list[str]),
        load=load,
    )
    values.update(overrides)
    return values


async def test_cache_reuses_dtos_preserves_literal_text_and_invalidates_changed_versions():
    redis = MemoryRedis()
    cache = ResultCache(redis)
    repository = SimpleNamespace(versions=AsyncMock(return_value=("first",)))
    load = AsyncMock(return_value=["<script>literal</script>"])
    values = arguments(repository, load)
    assert await cache.read(**values) == ["<script>literal</script>"]
    assert await cache.read(**values) == ["<script>literal</script>"]
    load.assert_awaited_once()
    repository.versions.return_value = ("changed",)
    await cache.read(**values)
    assert load.await_count == 2
    assert redis.ttls == [300, 300]


async def test_private_keys_include_owner_role_auth_version_and_every_filter():
    redis = MemoryRedis()
    cache = ResultCache(redis)
    repository = SimpleNamespace(versions=AsyncMock(return_value=("v", "global", "catalog")))
    load = AsyncMock(return_value=[])
    owner = uuid4()
    values = arguments(
        repository,
        load,
        domain="orders",
        owner=owner,
        role="CUSTOMER",
        filters={"status": None, "limit": 25, "cursor": None},
    )
    await cache.read(**values)
    for changed in [
        dict(owner=uuid4()),
        dict(role="COURIER"),
        dict(auth_version=1),
        dict(filters={"status": "NEW", "limit": 25, "cursor": None}),
        dict(filters={"status": None, "limit": 50, "cursor": None}),
        dict(filters={"status": None, "limit": 25, "cursor": "next"}),
    ]:
        await cache.read(**{**values, **changed})
    assert load.await_count == 7
    assert len(redis.values) == 7
    assert set(redis.ttls) == {30}
    with pytest.raises(ValueError):
        await cache.read(**{**values, "owner": None})


async def test_cache_outage_loads_sql_and_does_not_log_payload(caplog):
    redis = SimpleNamespace(
        getrange=AsyncMock(side_effect=ConnectionError()),
        eval=AsyncMock(side_effect=ConnectionError()),
    )
    load = AsyncMock(return_value=["private description"])
    repo = SimpleNamespace(versions=AsyncMock(return_value=("v",)))
    assert await ResultCache(redis).read(**arguments(repo, load)) == ["private description"]
    assert "private description" not in caplog.text
    load.assert_awaited_once()


async def test_changed_version_during_load_is_not_published_and_oversized_result_is_not_stored():
    redis = MemoryRedis()
    cache = ResultCache(redis)
    repo = SimpleNamespace(versions=AsyncMock(side_effect=[("old",), ("new",)]))
    await cache.read(**arguments(repo, AsyncMock(return_value=["value"])))
    assert not redis.values
    repo.versions = AsyncMock(return_value=("v",))
    await cache.read(**arguments(repo, AsyncMock(return_value=["x" * 262144])))
    assert not redis.values


async def test_concurrent_misses_share_only_serialized_results_and_cancelled_waiter_is_safe():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def load():
        calls.append(True)
        entered.set()
        await release.wait()
        return ["result"]

    cache = ResultCache(MemoryRedis())
    repo = SimpleNamespace(versions=AsyncMock(return_value=("v",)))
    values = arguments(repo, load)
    first = asyncio.create_task(cache.read(**values))
    await entered.wait()
    second = asyncio.create_task(cache.read(**values))
    await asyncio.sleep(0)
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    release.set()
    assert await first == ["result"]
    assert len(calls) == 1 and not cache._pending


async def test_bad_cache_json_and_loader_failure_do_not_leave_pending_entries():
    redis = MemoryRedis()
    cache = ResultCache(redis)
    repo = SimpleNamespace(versions=AsyncMock(return_value=("v",)))
    values = arguments(repo, AsyncMock(return_value=["ok"]))
    await cache.read(**values)
    for key in redis.values:
        redis.values[key] = b"{invalid"
    values["load"] = AsyncMock(side_effect=ValueError("failed SQL"))
    with pytest.raises(ValueError):
        await cache.read(**values)
    assert not cache._pending


async def test_occasions_cache_uses_one_hour_maximum_ttl():
    redis = MemoryRedis()
    repo = SimpleNamespace(versions=AsyncMock(return_value=("owner", "calendar")))
    await ResultCache(redis).read(
        **arguments(
            repo, AsyncMock(return_value=[]), domain="occasions", owner=uuid4(), role="CUSTOMER"
        )
    )
    assert redis.ttls == [3600]
