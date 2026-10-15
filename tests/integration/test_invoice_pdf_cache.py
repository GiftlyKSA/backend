import os
from uuid import uuid4

import pytest
from app.services.invoice_pdf_cache import _STORE
from redis.asyncio import Redis
from redis.exceptions import RedisError


async def test_pdf_namespace_evicts_only_its_oldest_cached_documents():
    redis = Redis.from_url(
        os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15"),
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    index = f"test:pdf-cache:{uuid4()}"
    keys = [f"{index}:{i}" for i in range(3)]
    try:
        try:
            await redis.ping()
        except RedisError:
            pytest.skip("Disposable Redis is unavailable.")
        for key in keys:
            await redis.eval(_STORE, 2, index, key, "document", 10, 2)
        assert await redis.zcard(index) == 2
        assert await redis.get(keys[0]) is None
        assert await redis.get(keys[1]) == "document"
        assert 0 < await redis.ttl(keys[2]) <= 10
    finally:
        try:
            if await redis.exists(index):
                await redis.delete(index, *keys)
        except RedisError:
            pass
        await redis.aclose()


@pytest.mark.parametrize("jump", [-7200, 7200], ids=["behind", "ahead"])
async def test_application_clock_jump_does_not_orphan_live_pdf_blobs(monkeypatch, jump):
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.services import invoice_pdf_cache

    from tests.unit.test_invoice_delivery_changes import invoice, items

    redis = Redis.from_url(
        os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15"), decode_responses=True
    )
    index = f"test:pdf-clock:{uuid4()}"
    keys = []
    clock = [time.time()]

    class ScopedRedis:
        async def get(self, key):
            return await redis.get(index + key)

        async def eval(self, script, count, index_key, key, *args):
            keys.append(index + key)
            return await redis.eval(script, count, index, index + key, *args)

    monkeypatch.setattr(
        invoice_pdf_cache, "time", SimpleNamespace(time=lambda: clock[0]), raising=False
    )
    monkeypatch.setattr(invoice_pdf_cache, "_MAX_ENTRIES", 2)
    monkeypatch.setattr(
        invoice_pdf_cache, "render_invoice_pdf", Mock(return_value=b"%PDF-1.4 safe")
    )
    try:
        try:
            await redis.ping()
        except RedisError:
            pytest.skip("Disposable Redis is unavailable.")
        cache = invoice_pdf_cache.InvoicePdfCache(ScopedRedis())
        for index_number in range(3):
            if index_number == 2:
                clock[0] += jump
            await cache.render(invoice(), items())
        assert len(keys) == 3
        assert sum([bool(await redis.exists(key)) for key in keys]) == 2
        assert await redis.zcard(index) == 2
        now = await redis.time()
        for key in await redis.zrange(index, 0, -1):
            assert 3595 < await redis.zscore(index, key) - now[0] <= 3601
    finally:
        await redis.delete(index, *keys)
        await redis.aclose()
