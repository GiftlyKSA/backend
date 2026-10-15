"""Media pre-sign and confirm.

The API never accepts bytes: it issues a pre-signed PUT URL with a SERVER-generated
key, and the client uploads directly to S3. Confirm HEADs the object and verifies its
actual type and contents through bounded isolated decoding; misleading image headers are rejected.
Keys are validated against a strict allow-list (no ``../``, no absolute paths).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.core.config import Environment, Settings
from app.core.exceptions import BadRequestError, ConflictError, StorageUnavailableError
from app.integrations.storage.base import ObjectHead, StorageClient
from app.models import MediaUpload
from app.repositories.media_repository import MediaRepository
from app.services.chat_media_validation import MEDIA_TYPES, verify_image_isolated

ALLOWED_IMAGE_TYPES = frozenset(MEDIA_TYPES["IMAGE"])
_image_slots = asyncio.Semaphore(2)
_UPLOAD_TTL_SECONDS = 300
_AVATAR_MAX_BYTES = 10 * 1024 * 1024
_AVATAR_GRANT_RETENTION = timedelta(days=1)
MAX_OUTSTANDING_UPLOADS = 20
MAX_OUTSTANDING_BYTES = 50 * 1024 * 1024
_PREFIX_BY_PURPOSE = {
    "ORDER_REQUEST": "orders/pending",
    "DELIVERY_PROOF": "orders/proof",
    "COURIER_AVATAR": "couriers/avatars",
}
# A safe key: only the known prefixes, a uuid, and a supported image suffix — nothing else.
_KEY_RE = re.compile(
    r"^(orders/pending|orders/proof|couriers/avatars)/[0-9a-f-]{36}\.(jpg|png|heic|heif)$"
)


@dataclass(frozen=True)
class _UploadSnapshot:
    storage_key: str
    content_type: str
    byte_size: int


TransactionBoundary = Callable[[], Awaitable[None]]


class MediaService:
    """Issues pre-signed uploads and confirms uploaded objects."""

    def __init__(
        self, storage: StorageClient, settings: Settings, uploads: MediaRepository
    ) -> None:
        """Wire the storage client and settings."""
        self._storage = storage
        self._settings = settings
        self._uploads = uploads
        self._prepared: dict[tuple[uuid.UUID, str, str], ObjectHead] = {}

    def _require_avatar_storage(self) -> None:
        if (
            self._settings.storage_provider == "fake"
            and self._settings.ENVIRONMENT is not Environment.TEST
        ):
            raise StorageUnavailableError()

    def _avatar_url(self, url: str) -> str:
        if (
            self._settings.ENVIRONMENT is Environment.TEST
            and self._settings.storage_provider == "fake"
        ):
            return url
        try:
            parsed = urlsplit(url)
            expected = urlsplit(self._settings.S3_ENDPOINT or "")
        except ValueError:
            raise StorageUnavailableError() from None
        if parsed.scheme != "https" or parsed.netloc != expected.netloc or parsed.fragment:
            raise StorageUnavailableError()
        return url

    def avatar_read_url(self, key: str) -> str:
        """Sign a temporary avatar reference after caller authorization."""
        self._require_avatar_storage()
        return self._avatar_url(self._storage.signed_read_url(key, ttl_seconds=_UPLOAD_TTL_SECONDS))

    async def request_upload_url(
        self,
        *,
        actor_id: uuid.UUID,
        purpose: str,
        content_type: str,
        byte_size: int,
        release_reads: TransactionBoundary | None = None,
        resume_writes: TransactionBoundary | None = None,
    ) -> tuple[str, str, int]:
        """Validate the request and return (upload_url, storage_key, expires_in).

        Raises:
            BadRequestError: Bad purpose, content type, or size.
        """
        prefix = _PREFIX_BY_PURPOSE.get(purpose)
        if prefix is None:
            raise BadRequestError("Unknown media purpose.")
        if purpose == "COURIER_AVATAR" and content_type not in {"image/jpeg", "image/png"}:
            raise BadRequestError("Courier avatars must be JPEG or PNG.")
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise BadRequestError("Only JPEG, PNG, HEIC or HEIF images are allowed.")
        max_bytes = self._settings.MAX_UPLOAD_BYTES
        if purpose == "COURIER_AVATAR":
            max_bytes = min(max_bytes, _AVATAR_MAX_BYTES)
            self._require_avatar_storage()
        if not 0 < byte_size <= max_bytes:
            raise BadRequestError("The file is too large.")

        ext = MEDIA_TYPES["IMAGE"][content_type]
        storage_key = f"{prefix}/{uuid.uuid4()}.{ext}"
        if release_reads is not None:
            await release_reads()
        url = await self._storage.create_upload_url(
            storage_key=storage_key,
            content_type=content_type,
            byte_size=byte_size,
            ttl_seconds=_UPLOAD_TTL_SECONDS,
        )
        if purpose == "COURIER_AVATAR":
            self._avatar_url(url)
        if resume_writes is not None:
            await resume_writes()
        await self._uploads.issue(
            storage_key=storage_key,
            actor_id=actor_id,
            purpose=purpose,
            content_type=content_type,
            byte_size=byte_size,
            max_count=MAX_OUTSTANDING_UPLOADS,
            max_bytes=MAX_OUTSTANDING_BYTES,
        )
        return url, storage_key, _UPLOAD_TTL_SECONDS

    async def confirm(
        self,
        storage_key: str,
        *,
        actor_id: uuid.UUID,
        release_reads: TransactionBoundary | None = None,
        resume_writes: TransactionBoundary | None = None,
    ) -> None:
        """Verify an uploaded object exists, is within size, and is a real image.

        Raises:
            BadRequestError: The key is malformed, missing, oversized, or not an
                image by magic bytes.
        """
        grant = await self._owned_grant(storage_key, actor_id)
        if grant.attached_at is not None or grant.deleting_at is not None:
            raise ConflictError("This media has already been attached.")
        snapshot = _UploadSnapshot(storage_key, grant.content_type, grant.byte_size)
        if release_reads is not None:
            await release_reads()
        await self._verify_object(snapshot)
        if resume_writes is not None:
            await resume_writes()
        current = await self._owned_grant(storage_key, actor_id, for_update=True)
        if (current.content_type, current.byte_size) != (snapshot.content_type, snapshot.byte_size):
            raise ConflictError("The upload changed during validation.")
        if not await self._uploads.mark_confirmed(storage_key, actor_id):
            raise ConflictError("This media has already been attached.")

    async def claim(self, storage_key: str, *, actor_id: uuid.UUID, purpose: str) -> ObjectHead:
        """Claim a confirmed upload once within the caller's attachment transaction."""
        prepared = self._prepared.get((actor_id, purpose, storage_key))
        grant = await self._owned_grant(storage_key, actor_id, for_update=prepared is not None)
        if grant.purpose != purpose or grant.confirmed_at is None:
            raise BadRequestError("The media is not confirmed for this purpose.")
        if grant.attached_at is not None or grant.deleting_at is not None:
            raise ConflictError("This media has already been attached.")
        if prepared is not None:
            if (grant.content_type, grant.byte_size) != (prepared.content_type, prepared.byte_size):
                raise ConflictError("The upload changed during validation.")
            head = prepared
        else:
            head = await self._verify_object(grant)
        if not await self._uploads.claim(storage_key, actor_id, purpose):
            raise ConflictError("This media has already been attached.")
        return head

    async def prepare_claims(
        self,
        storage_keys: list[str],
        *,
        actor_id: uuid.UUID,
        purpose: str,
        release_reads: TransactionBoundary,
    ) -> None:
        """Validate immutable uploads outside the caller's attachment transaction."""
        if len(storage_keys) > 5 or len(storage_keys) != len(set(storage_keys)):
            raise ConflictError("Provide at most five unique media keys.")
        snapshots = []
        for grant in await self._owned_grants(storage_keys, actor_id):
            key = grant.storage_key
            if grant.purpose != purpose or grant.confirmed_at is None:
                raise BadRequestError("The media is not confirmed for this purpose.")
            if grant.attached_at is not None or grant.deleting_at is not None:
                raise ConflictError("This media has already been attached.")
            snapshots.append(_UploadSnapshot(key, grant.content_type, grant.byte_size))
        await release_reads()
        prepared = {}
        for snapshot in snapshots:
            prepared[(actor_id, purpose, snapshot.storage_key)] = await self._verify_object(
                snapshot
            )
        self._prepared = prepared

    async def claim_many(
        self, storage_keys: list[str], *, actor_id: uuid.UUID, purpose: str
    ) -> dict[str, ObjectHead]:
        """Claim unique keys in stable order to avoid opposing row-lock orders."""
        if len(storage_keys) > 5 or len(storage_keys) != len(set(storage_keys)):
            raise ConflictError("Provide at most five unique media keys.")
        if not storage_keys:
            return {}
        keys = sorted(storage_keys)
        heads = {
            key: self._prepared[(actor_id, purpose, key)]
            for key in keys
            if (actor_id, purpose, key) in self._prepared
        }
        if len(heads) != len(keys):
            snapshots = []
            for grant in await self._owned_grants(keys, actor_id):
                self._check_claimable(grant, purpose)
                snapshots.append(
                    _UploadSnapshot(grant.storage_key, grant.content_type, grant.byte_size)
                )
            for snapshot in snapshots:
                if snapshot.storage_key not in heads:
                    heads[snapshot.storage_key] = await self._verify_object(snapshot)
        for grant in await self._owned_grants(keys, actor_id, for_update=True):
            self._check_claimable(grant, purpose)
            head = heads[grant.storage_key]
            if (grant.content_type, grant.byte_size) != (head.content_type, head.byte_size):
                raise ConflictError("The upload changed during validation.")
        claimed = await self._uploads.claim_confirmed_many(keys, actor_id, purpose)
        if claimed != set(keys):
            raise ConflictError("This media has already been attached.")
        return heads

    @staticmethod
    def _check_claimable(grant: MediaUpload, purpose: str) -> None:
        if grant.purpose != purpose or grant.confirmed_at is None:
            raise BadRequestError("The media is not confirmed for this purpose.")
        if grant.attached_at is not None or grant.deleting_at is not None:
            raise ConflictError("This media has already been attached.")

    async def _owned_grants(
        self, keys: list[str], actor_id: uuid.UUID, *, for_update: bool = False
    ) -> list[MediaUpload]:
        for key in keys:
            self.validate_key(key)
        if not keys:
            return []
        grants = {
            grant.storage_key: grant
            for grant in await self._uploads.get_many(keys, for_update=for_update)
        }
        return [self._check_owned_grant(grants.get(key), key, actor_id) for key in sorted(keys)]

    async def _owned_grant(
        self, storage_key: str, actor_id: uuid.UUID, *, for_update: bool = False
    ) -> MediaUpload:
        self.validate_key(storage_key)
        grant = await self._uploads.get(storage_key, for_update=for_update)
        return self._check_owned_grant(grant, storage_key, actor_id)

    def _check_owned_grant(
        self, grant: MediaUpload | None, storage_key: str, actor_id: uuid.UUID
    ) -> MediaUpload:
        if grant is None or grant.owner_user_id != actor_id:
            raise BadRequestError("The upload key is not available to this account.")
        if storage_key.startswith("couriers/avatars/"):
            self._require_avatar_storage()
            if grant.created_at < datetime.now(UTC) - _AVATAR_GRANT_RETENTION:
                raise BadRequestError("The upload grant expired; request a new upload URL.")
        return grant

    async def _verify_object(self, grant: MediaUpload | _UploadSnapshot) -> ObjectHead:
        storage_key = grant.storage_key
        avatar = storage_key.startswith("couriers/avatars/")
        if avatar:
            self._require_avatar_storage()
        head = await self._storage.head_object(storage_key)
        if head is None or not head.exists:
            raise BadRequestError("The uploaded object was not found.")
        max_bytes = (
            min(self._settings.MAX_UPLOAD_BYTES, _AVATAR_MAX_BYTES)
            if avatar
            else self._settings.MAX_UPLOAD_BYTES
        )
        if head.byte_size != grant.byte_size or head.byte_size > max_bytes:
            raise BadRequestError("The uploaded file size does not match the upload request.")
        if (
            head.content_type != grant.content_type
            or head.content_type not in ALLOWED_IMAGE_TYPES
            or (avatar and head.content_type not in {"image/jpeg", "image/png"})
        ):
            raise BadRequestError("The uploaded file is not a permitted image.")
        try:
            async with asyncio.timeout(25):
                async with _image_slots:
                    body = await self._storage.read_bounded_object(
                        storage_key, max_bytes=grant.byte_size
                    )
                    if len(body) != grant.byte_size:
                        raise BadRequestError(
                            "The uploaded file size does not match its upload grant."
                        )
                    await verify_image_isolated(body, grant.content_type)
        except ValueError as exc:
            raise BadRequestError("The uploaded file exceeds its declared size.") from exc
        except TimeoutError as exc:
            from app.core.exceptions import MediaValidationUnavailableError

            raise MediaValidationUnavailableError() from exc
        return head

    @staticmethod
    def validate_key(storage_key: str) -> None:
        """Reject any key not matching the strict allow-list (path traversal, etc.).

        Raises:
            ValidationDomainError: The key is not a well-formed, expected S3 key.
        """
        if "\x00" in storage_key or ".." in storage_key or not _KEY_RE.match(storage_key):
            raise BadRequestError("Invalid storage key.")
