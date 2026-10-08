"""Real S3 + CloudFront storage client (SPEC SECTION 16.1-2, 17.3).

Uploads are pre-signed PUTs pinned to a content-type and exact content length so a
URL cannot be used to upload a larger object. Post-upload the object is HEADed and its
real content type is verified by magic bytes, not the declared header. Reads use
short-TTL signed CloudFront URLs. A synchronous boto call would be wrapped in
``run_in_threadpool``; aioboto3 is async so calls are awaited directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.signers import CloudFrontSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.integrations.storage.base import STORAGE_READ_CHUNK_BYTES, ObjectHead, StorageClient

_IMAGE_MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}


class S3StorageClient(StorageClient):
    """Talks to a private S3 bucket and signs CloudFront read URLs."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        cloudfront_domain: str,
        cloudfront_key_pair_id: str,
        cloudfront_private_key: str,
    ) -> None:
        """Hold the bucket, region, and CDN domain."""
        self._bucket = bucket
        self._region = region
        self._cloudfront_domain = cloudfront_domain
        self._cloudfront_key_pair_id = cloudfront_key_pair_id
        key = serialization.load_pem_private_key(
            cloudfront_private_key.replace("\\n", "\n").encode("utf-8"), password=None
        )
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("CLOUDFRONT_PRIVATE_KEY must contain an RSA private key.")
        self._cloudfront_private_key = key
        self._cloudfront_signer = CloudFrontSigner(
            self._cloudfront_key_pair_id, self._sign_cloudfront_policy
        )
        self._client_config = Config(signature_version="s3v4")
        self._session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        self._condition = asyncio.Condition()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._s3: Any = None
        self._s3_context: Any = None
        self._active = 0
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        loop = asyncio.get_running_loop()
        async with self._condition:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("S3 storage client belongs to a different event loop")
            if self._closing or self._closed:
                raise RuntimeError("S3 storage client is closing or closed")
            self._loop = loop
            if self._s3 is None:
                context = self._session.client(
                    "s3", region_name=self._region, config=self._client_config
                )
                self._s3 = await context.__aenter__()
                self._s3_context = context
            self._active += 1
            s3 = self._s3
        try:
            yield s3
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    async def aclose(self) -> None:
        """Drain active operations and close the pooled S3 connection."""
        loop = asyncio.get_running_loop()
        async with self._condition:
            if self._loop is not None and self._loop is not loop:
                raise RuntimeError("S3 storage client belongs to a different event loop")
            if self._close_task is None:
                self._closing = True
                self._close_task = asyncio.create_task(self._finish_close())
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _finish_close(self) -> None:
        """Own shutdown even when a caller awaiting it is cancelled."""
        try:
            async with self._condition:
                while self._active:
                    await self._condition.wait()
                context = self._s3_context
            if context is not None:
                await context.__aexit__(None, None, None)
        finally:
            async with self._condition:
                self._s3 = None
                self._s3_context = None
                self._closed = True
                self._condition.notify_all()

    async def create_upload_url(
        self, *, storage_key: str, content_type: str, byte_size: int, ttl_seconds: int
    ) -> str:
        """Return a create-only pre-signed PUT URL pinned to type and size."""
        async with self._client() as s3:
            url: str = await s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": storage_key,
                    "ContentType": content_type,
                    "ContentLength": byte_size,
                    "IfNoneMatch": "*",
                },
                ExpiresIn=ttl_seconds,
            )
        return url

    async def head_object(self, storage_key: str) -> ObjectHead | None:
        """HEAD the object; return its size and content type, or None if missing."""
        async with self._client() as s3:
            try:
                resp = await s3.head_object(Bucket=self._bucket, Key=storage_key)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
        return ObjectHead(
            exists=True,
            byte_size=int(resp.get("ContentLength", 0)),
            content_type=str(resp.get("ContentType", "")),
        )

    async def delete_object(self, storage_key: str) -> None:
        """Delete an abandoned object; S3 treats a missing key as success."""
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=storage_key)

    async def verify_image_magic_bytes(self, storage_key: str, content_type: str) -> bool:
        """Read the first bytes and confirm they match the issued image type."""
        async with self._client() as s3:
            resp = await s3.get_object(Bucket=self._bucket, Key=storage_key, Range="bytes=0-15")
            body = resp["Body"]
            try:
                head = await body.read()
            finally:
                body.close()
        signature = _IMAGE_MAGIC.get(content_type)
        return signature is not None and head.startswith(signature)

    async def read_bounded_object(self, storage_key: str, *, max_bytes: int) -> bytes:
        """Keep the bounded byte API for image validation and existing callers."""
        result = bytearray()
        async for chunk in self.iter_bounded_object(storage_key, max_bytes=max_bytes):
            result.extend(chunk)
        return bytes(result)

    async def iter_bounded_object(
        self, storage_key: str, *, max_bytes: int
    ) -> AsyncGenerator[bytes, None]:
        """Stream bounded private chunks, always closing the S3 response body."""
        if max_bytes < 0:
            raise ValueError("Private media size must not be negative.")
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=storage_key)
            body = response["Body"]
            total = 0
            try:
                while chunk := await body.read(
                    min(STORAGE_READ_CHUNK_BYTES, max_bytes + 1 - total)
                ):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Private media exceeds the declared size.")
                    yield chunk
            finally:
                body.close()

    def signed_read_url(self, storage_key: str, *, ttl_seconds: int) -> str:
        """Return a short-lived, RSA-signed CloudFront read URL."""
        signed = cast(
            str,
            self._cloudfront_signer.generate_presigned_url(
                f"https://{self._cloudfront_domain}/{storage_key}",
                date_less_than=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            ),
        )
        return f"{signed}&Hash-Algorithm=SHA256"

    def _sign_cloudfront_policy(self, message: bytes) -> bytes:
        """Sign a CloudFront canned policy using AWS's documented algorithm."""
        return self._cloudfront_private_key.sign(
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
