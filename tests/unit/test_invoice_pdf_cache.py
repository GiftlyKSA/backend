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
