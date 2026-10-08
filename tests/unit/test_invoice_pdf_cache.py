from unittest.mock import AsyncMock, Mock

import pytest

from tests.unit.test_invoice_delivery_changes import invoice, items


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, script, count, index, key, value, *args):
        self.values[key] = value
        return 1


async def test_pdf_cache_reuses_render_and_refreshes_on_item_or_payment_change(monkeypatch):
    from app.services.invoice_pdf_cache import InvoicePdfCache

    renderer = Mock(return_value=b"%PDF-1.4 test")
    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", renderer)
    cache, stored, lines = InvoicePdfCache(MemoryRedis()), invoice(), items()
    assert await cache.render(stored, lines) == b"%PDF-1.4 test"
    assert await cache.render(stored, lines) == b"%PDF-1.4 test"
    assert renderer.call_count == 1
    lines[0].title = "Corrected title"
    await cache.render(stored, lines)
    assert renderer.call_count == 2
    stored.status = "PAID"
    await cache.render(stored, lines)
    assert renderer.call_count == 3


async def test_cache_outage_does_not_prevent_owned_pdf_generation(monkeypatch):
    from app.services.invoice_pdf_cache import InvoicePdfCache

    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("unavailable")
    renderer = Mock(return_value=b"%PDF-1.4 test")
    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", renderer)
    assert await InvoicePdfCache(redis).render(invoice(), items()) == b"%PDF-1.4 test"


@pytest.mark.parametrize("payload", ["bad", "a" * 400000], ids=["malformed", "oversized"])
async def test_invalid_cached_document_is_ignored(monkeypatch, payload):
    from app.services.invoice_pdf_cache import InvoicePdfCache

    redis = AsyncMock()
    redis.get.return_value = payload
    monkeypatch.setattr(
        "app.services.invoice_pdf_cache.render_invoice_pdf", Mock(return_value=b"%PDF-1.4 safe")
    )
    assert await InvoicePdfCache(redis).render(invoice(), items()) == b"%PDF-1.4 safe"


async def test_cancelled_pdf_requests_keep_thread_capacity_until_render_finishes(monkeypatch):
    import asyncio
    import threading

    from app.services.invoice_pdf_cache import InvoicePdfCache

    loop = asyncio.get_running_loop()
    started = asyncio.Queue()
    release = threading.Event()

    def render(*args):
        loop.call_soon_threadsafe(started.put_nowait, True)
        assert release.wait(5)
        return b"%PDF-1.4 test"

    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", render)
    cache = InvoicePdfCache(MemoryRedis())
    active = [asyncio.create_task(cache.render(invoice(), items())) for _ in range(4)]
    fifth = None
    try:
        for _ in active:
            await asyncio.wait_for(started.get(), 2)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        fifth = asyncio.create_task(cache.render(invoice(), items()))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(started.get(), 0.1)
    finally:
        release.set()
        await asyncio.gather(*active, return_exceptions=True)
        if fifth is not None:
            await asyncio.wait_for(fifth, 3)


async def test_cached_pdf_expires_after_one_hour_without_extending_hits(monkeypatch):
    from app.services.invoice_pdf_cache import InvoicePdfCache

    clock = [0]
    expiry = [0]

    class ExpiringRedis(MemoryRedis):
        async def get(self, key):
            return await super().get(key) if clock[0] < expiry[0] else None

        async def eval(self, script, count, index, key, value, ttl, *args):
            assert ttl == 3600
            expiry[0] = clock[0] + ttl
            return await super().eval(script, count, index, key, value)

    renderer = Mock(return_value=b"%PDF-1.4 test")
    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", renderer)
    cache, stored, lines = InvoicePdfCache(ExpiringRedis()), invoice(), items()
    await cache.render(stored, lines)
    clock[0] = 3599
    await cache.render(stored, lines)
    assert renderer.call_count == 1
    clock[0] = 3600
    await cache.render(stored, lines)
    assert renderer.call_count == 2


async def test_identical_pdf_misses_share_render_despite_cancelled_waiter(monkeypatch):
    import asyncio
    import threading

    from app.services.invoice_pdf_cache import InvoicePdfCache

    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def render(*args):
        calls.append(True)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return b"%PDF-1.4 shared"

    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", render)
    redis = AsyncMock()
    redis.get.return_value = None
    cache = InvoicePdfCache(redis)
    stored, lines = invoice(), items()
    first = asyncio.create_task(cache.render(stored, lines))
    second = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        second = asyncio.create_task(cache.render(stored, lines))
        await asyncio.sleep(0.05)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        assert await asyncio.wait_for(second, 2) == b"%PDF-1.4 shared"
        assert len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


async def test_pdf_fingerprints_remain_separate_and_inflight_capacity_is_bounded(monkeypatch):
    import asyncio
    import copy
    import threading

    from app.services.invoice_pdf_cache import InvoicePdfCache

    started = asyncio.Queue()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def render(stored, lines):
        loop.call_soon_threadsafe(started.put_nowait, stored.status)
        assert release.wait(5)
        return f"%PDF-{stored.status}".encode()

    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", render)
    monkeypatch.setattr("app.services.invoice_pdf_cache._MAX_ENTRIES", 2)
    redis = AsyncMock()
    redis.get.return_value = None
    cache = InvoicePdfCache(redis)
    stored = invoice()
    changed = copy.copy(stored)
    changed.status = "PAID"
    tasks = [asyncio.create_task(cache.render(value, items())) for value in (stored, changed)]
    third = None
    try:
        for _ in tasks:
            await asyncio.wait_for(started.get(), 2)
        third = asyncio.create_task(cache.render(invoice(), items()))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(started.get(), 0.05)
        assert len(cache._inflight) == 2
        release.set()
        results = await asyncio.gather(*tasks, third)
        assert results[0] != results[1]
        await asyncio.sleep(0)
        assert not cache._inflight
    finally:
        release.set()
        await asyncio.gather(*tasks, *([third] if third else []), return_exceptions=True)


async def test_failed_shared_pdf_render_is_removed_and_retry_succeeds(monkeypatch):
    from app.services.invoice_pdf_cache import InvoicePdfCache

    renderer = Mock(side_effect=[ValueError("invalid document"), b"%PDF-1.4 retry"])
    monkeypatch.setattr("app.services.invoice_pdf_cache.render_invoice_pdf", renderer)
    cache = InvoicePdfCache(MemoryRedis())
    stored, lines = invoice(), items()
    with pytest.raises(ValueError, match="invalid document"):
        await cache.render(stored, lines)
    assert await cache.render(stored, lines) == b"%PDF-1.4 retry"
    assert not cache._inflight
