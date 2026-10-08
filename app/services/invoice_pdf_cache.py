"""Bounded private PDF reuse after fresh invoice ownership and content reads."""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import time
from pathlib import Path

from redis.asyncio import Redis

from app.models import Invoice, InvoiceItem
from app.services.invoice_pdf import render_invoice_pdf

_logger = logging.getLogger(__name__)
_TTL_SECONDS = 3600
_MAX_BYTES = 262144
_MAX_ENTRIES = 128
_TEMPLATE_VERSION = hashlib.sha256(
    Path(__file__).with_name("invoice_pdf.py").read_bytes()
).hexdigest()
_INVOICE_FIELDS = (
    "id",
    "order_id",
    "status",
    "currency",
    "issued_at",
    "paid_at",
    "items_net_amount",
    "courier_fee_amount",
    "service_fee_amount",
    "discount_amount",
    "total_amount",
)
_ITEM_FIELDS = (
    "position",
    "title",
    "quantity",
    "unit_price_amount",
    "line_discount_amount",
    "line_total_amount",
)
_STORE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[3])
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[1], tonumber(ARGV[3]) + tonumber(ARGV[2]), KEYS[2])
local excess = redis.call('ZCARD', KEYS[1]) - tonumber(ARGV[4])
if excess > 0 then
    local oldest = redis.call('ZPOPMIN', KEYS[1], excess)
    for i = 1, #oldest, 2 do redis.call('DEL', oldest[i]) end
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return 1
"""


def _fingerprint(invoice: Invoice, items: list[InvoiceItem]) -> str:
    content = [
        _TEMPLATE_VERSION,
        [str(getattr(invoice, name)) for name in _INVOICE_FIELDS],
        [[str(getattr(item, name)) for name in _ITEM_FIELDS] for item in items],
    ]
    return hashlib.sha256(json.dumps(content, ensure_ascii=True).encode()).hexdigest()


class InvoicePdfCache:
    """Cap cache memory and render concurrency; Redis failures fall back to rendering."""

    def __init__(self, redis: Redis) -> None:
        """Use the application pool and at most four rendering threads per process."""
        self._redis = redis
        self._slots = asyncio.Semaphore(4)

    async def _read(self, key: str, fingerprint: str) -> bytes | None:
        try:
            async with asyncio.timeout(0.1):
                stored = await self._redis.get(key)
            if not isinstance(stored, str) or len(stored) > _MAX_BYTES * 4 // 3 + 100:
                return None
            version, _, encoded = stored.partition(":")
            if version != fingerprint:
                return None
            document = base64.b64decode(encoded, validate=True)
            if len(document) <= _MAX_BYTES and document.startswith(b"%PDF-"):
                return document
        except (binascii.Error, ValueError):
            return None
        except Exception:  # noqa: BLE001 - an optional cache cannot break an owned read
            _logger.warning("Invoice PDF cache read unavailable.")
        return None

    async def render(self, invoice: Invoice, items: list[InvoiceItem]) -> bytes:
        """Reuse only identical rendered content; callers must authorize before calling."""
        fingerprint = _fingerprint(invoice, items)
        key = f"cache:invoice-pdf:v2:{invoice.id}"
        cached = await self._read(key, fingerprint)
        if cached is not None:
            return cached
        await self._slots.acquire()
        render_task: asyncio.Task[bytes] | None = None
        try:
            cached = await self._read(key, fingerprint)
            if cached is not None:
                return cached
            render_task = asyncio.create_task(asyncio.to_thread(render_invoice_pdf, invoice, items))
            render_task.add_done_callback(self._render_finished)
            document = await asyncio.shield(render_task)
            if len(document) <= _MAX_BYTES:
                try:
                    async with asyncio.timeout(0.1):
                        await self._redis.eval(
                            _STORE,
                            2,
                            "cache:invoice-pdf:v2:index",
                            key,
                            f"{fingerprint}:{base64.b64encode(document).decode('ascii')}",
                            _TTL_SECONDS,
                            time.time(),
                            _MAX_ENTRIES,
                        )
                except Exception:  # noqa: BLE001 - document generation already succeeded
                    _logger.warning("Invoice PDF cache write unavailable.")
            return document
        finally:
            if render_task is None:
                self._slots.release()

    def _render_finished(self, task: asyncio.Task[bytes]) -> None:
        self._slots.release()
        if not task.cancelled():
            task.exception()
