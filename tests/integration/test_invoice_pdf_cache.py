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
        for i, key in enumerate(keys):
            await redis.eval(_STORE, 2, index, key, "document", 10, 100 + i, 2)
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
