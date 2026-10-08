"""The object-storage contract.

Zero-proxy media: the API never streams bytes. It issues a pre-signed PUT URL, the
client uploads straight to S3, then confirms the key. Reads go through short-TTL
signed CDN URLs generated only after an ownership check. Services depend on this ABC;
only the Real/Fake implementations know the S3/CloudFront wire format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass

STORAGE_READ_CHUNK_BYTES = 65536


@dataclass(frozen=True)
class ObjectHead:
    """The result of heading a stored object."""

    exists: bool
    byte_size: int
    content_type: str


class StorageClient(ABC):
    """Issues pre-signed uploads, heads objects, and signs read URLs."""

    @abstractmethod
    async def create_upload_url(
        self, *, storage_key: str, content_type: str, byte_size: int, ttl_seconds: int
    ) -> str:
        """Return a pre-signed PUT URL pinned to a content-type and exact size."""

    @abstractmethod
    async def head_object(self, storage_key: str) -> ObjectHead | None:
        """Return object metadata, or None if it does not exist."""

    @abstractmethod
    async def delete_object(self, storage_key: str) -> None:
        """Delete an abandoned object; a missing object counts as success."""

    @abstractmethod
    async def verify_image_magic_bytes(self, storage_key: str, content_type: str) -> bool:
        """Return whether the object's bytes match its issued image type."""

    @abstractmethod
    def signed_read_url(self, storage_key: str, *, ttl_seconds: int) -> str:
        """Return a short-TTL signed CDN read URL for an object."""

    async def read_bounded_object(self, storage_key: str, *, max_bytes: int) -> bytes:
        """Read private bytes for validation; unsupported providers fail closed."""
        raise NotImplementedError("Bounded private media reads are not configured.")

    async def iter_bounded_object(
        self, storage_key: str, *, max_bytes: int
    ) -> AsyncGenerator[bytes, None]:
        """Adapt legacy bounded readers; streaming providers override this method."""
        body = await self.read_bounded_object(storage_key, max_bytes=max_bytes)
        if len(body) > max_bytes:
            raise ValueError("Private media exceeds the declared size.")
        for offset in range(0, len(body), STORAGE_READ_CHUNK_BYTES):
            yield body[offset : offset + STORAGE_READ_CHUNK_BYTES]
