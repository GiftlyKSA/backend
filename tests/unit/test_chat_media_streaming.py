"""Bounded S3 streaming releases private response bodies on every exit."""

import asyncio
import tracemalloc
from contextlib import aclosing, asynccontextmanager
from types import SimpleNamespace

import pytest
from app.integrations.storage.real import S3StorageClient


class Body:
    def __init__(self, size):
        self.remaining = size
        self.reads = []
        self.closed = False

    async def read(self, size):
        self.reads.append(size)
        count = min(size, self.remaining)
        self.remaining -= count
        return b"x" * count

    def close(self):
        self.closed = True


def client_for(monkeypatch, body):
    client = object.__new__(S3StorageClient)
    client._bucket = "test-private-bucket"

    async def get_object(**kwargs):
        assert kwargs == {"Bucket": "test-private-bucket", "Key": "chat/key"}
        return {"Body": body}

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(get_object=get_object)

    monkeypatch.setattr(client, "_client", connection)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [125829120, 125829121])
async def test_s3_stream_is_chunk_bounded_and_rejects_excess(monkeypatch, size):
    body = Body(size)
    client = client_for(monkeypatch, body)
    total, largest = 0, 0

    async def consume():
        nonlocal total, largest
        async for chunk in client.iter_bounded_object("chat/key", max_bytes=125829120):
            total += len(chunk)
            largest = max(largest, len(chunk))

    tracemalloc.start()
    try:
        if size > 125829120:
            with pytest.raises(ValueError):
                await consume()
        else:
            await consume()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024
    assert total == 125829120
    assert largest <= 65536
    assert max(body.reads) <= 65536
    assert body.closed


@pytest.mark.asyncio
async def test_s3_stream_closes_body_on_cancelled_read(monkeypatch):
    body = Body(9)
    started = asyncio.Event()

    async def read(size):
        started.set()
        await asyncio.Event().wait()

    body.read = read
    client = client_for(monkeypatch, body)

    async def consume():
        async for _ in client.iter_bounded_object("chat/key", max_bytes=9):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed


@pytest.mark.asyncio
async def test_s3_stream_closes_body_when_consumer_stops(monkeypatch):
    body = Body(100000)
    client = client_for(monkeypatch, body)
    async with aclosing(client.iter_bounded_object("chat/key", max_bytes=100000)) as stream:
        assert len(await anext(stream)) == 65536
    assert body.closed
    assert body.remaining == 34464


@pytest.mark.asyncio
async def test_fake_stream_and_legacy_provider_keep_byte_api_compatibility():
    from app.core.config import Environment
    from app.integrations.storage.base import StorageClient
    from app.integrations.storage.fake import FakeStorageClient

    client = FakeStorageClient(Environment.TEST)
    client.recorded_bytes["chat/key"] = b"x" * 100000
    assert len(await client.read_bounded_object("chat/key", max_bytes=100000)) == 100000
    assert [
        len(chunk) async for chunk in client.iter_bounded_object("chat/key", max_bytes=100000)
    ] == [65536, 34464]
    assert [
        len(chunk)
        async for chunk in StorageClient.iter_bounded_object(client, "chat/key", max_bytes=100000)
    ] == [65536, 34464]
    with pytest.raises(ValueError):
        async for _ in client.iter_bounded_object("chat/key", max_bytes=99999):
            pass
