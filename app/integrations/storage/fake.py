"""In-memory storage double.

Lets the full media + order flow run with no S3. ``create_upload_url`` registers the
key so a later ``head_object`` reports it, mirroring a completed client PUT; magic-byte
verification is reported valid (there are no real bytes to inspect in the fake).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from app.core.config import Environment
from app.integrations._guard import forbid_in_production
from app.integrations.storage.base import STORAGE_READ_CHUNK_BYTES, ObjectHead, StorageClient


class FakeStorageClient(StorageClient):
    """Records pre-signed keys in memory and reports them as uploaded."""

    def __init__(self, environment: Environment) -> None:
        """Refuse construction in production, then start with an empty store."""
        forbid_in_production(environment, type(self).__name__)
        self._objects: dict[str, ObjectHead] = {}
        self.recorded_bytes: dict[str, bytes] = {}

    async def create_upload_url(
        self, *, storage_key: str, content_type: str, byte_size: int, ttl_seconds: int
    ) -> str:
        """Register the key as uploaded and return a local simulate URL."""
        self._objects[storage_key] = ObjectHead(
            exists=True, byte_size=byte_size, content_type=content_type
        )
        return f"http://localhost:3000/dev/upload/{storage_key}"

    async def head_object(self, storage_key: str) -> ObjectHead | None:
        """Return the recorded object metadata, or None."""
        return self._objects.get(storage_key)

    async def delete_object(self, storage_key: str) -> None:
        """Remove an abandoned test object."""
        self._objects.pop(storage_key, None)
        self.recorded_bytes.pop(storage_key, None)

    async def verify_image_magic_bytes(self, storage_key: str, content_type: str) -> bool:
        """Report a recorded object as a valid image."""
        head = self._objects.get(storage_key)
        return head is not None and head.content_type == content_type

    def signed_read_url(self, storage_key: str, *, ttl_seconds: int) -> str:
        """Return a deterministic fake CDN URL."""
        return f"http://localhost:3000/dev/cdn/{storage_key}?ttl={ttl_seconds}"

    async def read_bounded_object(self, storage_key: str, *, max_bytes: int) -> bytes:
        """Require actual test bytes; never pretend a recording passed validation."""
        body = self.recorded_bytes.get(storage_key, b"")
        if len(body) > max_bytes:
            raise ValueError("Private media exceeds the declared size.")
        return body

    async def iter_bounded_object(
        self, storage_key: str, *, max_bytes: int
    ) -> AsyncGenerator[bytes, None]:
        """Yield bounded chunks from actual test bytes without duplicating the object."""
        body = await self.read_bounded_object(storage_key, max_bytes=max_bytes)
        for offset in range(0, len(body), STORAGE_READ_CHUNK_BYTES):
            yield body[offset : offset + STORAGE_READ_CHUNK_BYTES]
