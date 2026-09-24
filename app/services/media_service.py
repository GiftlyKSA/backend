"""Media pre-sign and confirm (SPEC SECTION 16.1, 17.3, 20.C).

The API never accepts bytes: it issues a pre-signed PUT URL with a SERVER-generated
key, and the client uploads directly to S3. Confirm HEADs the object and verifies its
real content type by magic bytes — a ``.jpg`` that is actually a script is rejected.
Keys are validated against a strict allow-list (no ``../``, no absolute paths).
"""

from __future__ import annotations

import re
import uuid

from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError
from app.integrations.storage.base import ObjectHead, StorageClient
from app.models import MediaUpload
from app.repositories.media_repository import MediaRepository

ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png"})
_UPLOAD_TTL_SECONDS = 300
_PREFIX_BY_PURPOSE = {
    "ORDER_REQUEST": "orders/pending",
    "DELIVERY_PROOF": "orders/proof",
}
# A safe key: only the known prefixes, a uuid, and a .jpg/.png suffix — nothing else.
_KEY_RE = re.compile(r"^(orders/pending|orders/proof)/[0-9a-f-]{36}\.(jpg|png)$")


class MediaService:
    """Issues pre-signed uploads and confirms uploaded objects."""

    def __init__(
        self, storage: StorageClient, settings: Settings, uploads: MediaRepository
    ) -> None:
        """Wire the storage client and settings."""
        self._storage = storage
        self._settings = settings
        self._uploads = uploads

    async def request_upload_url(
        self, *, actor_id: uuid.UUID, purpose: str, content_type: str, byte_size: int
    ) -> tuple[str, str, int]:
        """Validate the request and return (upload_url, storage_key, expires_in).

        Raises:
            BadRequestError: Bad purpose, content type, or size.
        """
        prefix = _PREFIX_BY_PURPOSE.get(purpose)
        if prefix is None:
            raise BadRequestError("Unknown media purpose.")
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise BadRequestError("Only JPEG or PNG images are allowed.")
        if not 0 < byte_size <= self._settings.MAX_UPLOAD_BYTES:
            raise BadRequestError("The file is too large.")

        ext = "jpg" if content_type == "image/jpeg" else "png"
        storage_key = f"{prefix}/{uuid.uuid4()}.{ext}"
        url = await self._storage.create_upload_url(
            storage_key=storage_key,
            content_type=content_type,
            byte_size=byte_size,
            ttl_seconds=_UPLOAD_TTL_SECONDS,
        )
        await self._uploads.issue(
            storage_key=storage_key,
            actor_id=actor_id,
            purpose=purpose,
            content_type=content_type,
            byte_size=byte_size,
        )
        return url, storage_key, _UPLOAD_TTL_SECONDS

    async def confirm(self, storage_key: str, *, actor_id: uuid.UUID) -> None:
        """Verify an uploaded object exists, is within size, and is a real image.

        Raises:
            BadRequestError: The key is malformed, missing, oversized, or not an
                image by magic bytes.
        """
        grant = await self._owned_grant(storage_key, actor_id)
        if grant.attached_at is not None:
            raise ConflictError("This media has already been attached.")
        await self._verify_object(grant)
        if not await self._uploads.mark_confirmed(storage_key, actor_id):
            raise ConflictError("This media has already been attached.")

    async def claim(self, storage_key: str, *, actor_id: uuid.UUID, purpose: str) -> ObjectHead:
        """Claim a confirmed upload once within the caller's attachment transaction."""
        grant = await self._owned_grant(storage_key, actor_id)
        if grant.purpose != purpose or grant.confirmed_at is None:
            raise BadRequestError("The media is not confirmed for this purpose.")
        if grant.attached_at is not None:
            raise ConflictError("This media has already been attached.")
        head = await self._verify_object(grant)
        if not await self._uploads.claim(storage_key, actor_id, purpose):
            raise ConflictError("This media has already been attached.")
        return head

    async def claim_many(
        self, storage_keys: list[str], *, actor_id: uuid.UUID, purpose: str
    ) -> dict[str, ObjectHead]:
        """Claim unique keys in stable order to avoid opposing row-lock orders."""
        if len(storage_keys) != len(set(storage_keys)):
            raise ConflictError("Duplicate media keys are not allowed.")
        heads: dict[str, ObjectHead] = {}
        for storage_key in sorted(storage_keys):
            heads[storage_key] = await self.claim(storage_key, actor_id=actor_id, purpose=purpose)
        return heads

    async def _owned_grant(self, storage_key: str, actor_id: uuid.UUID) -> MediaUpload:
        self.validate_key(storage_key)
        grant = await self._uploads.get(storage_key)
        if grant is None or grant.owner_user_id != actor_id:
            raise BadRequestError("The upload key is not available to this account.")
        return grant

    async def _verify_object(self, grant: MediaUpload) -> ObjectHead:
        storage_key = grant.storage_key
        head = await self._storage.head_object(storage_key)
        if head is None or not head.exists:
            raise BadRequestError("The uploaded object was not found.")
        if head.byte_size != grant.byte_size or head.byte_size > self._settings.MAX_UPLOAD_BYTES:
            raise BadRequestError("The uploaded file size does not match the upload request.")
        if head.content_type != grant.content_type or head.content_type not in ALLOWED_IMAGE_TYPES:
            raise BadRequestError("The uploaded file is not a permitted image.")
        if not await self._storage.verify_image_magic_bytes(storage_key, grant.content_type):
            raise BadRequestError("The uploaded file is not a valid image.")
        return head

    @staticmethod
    def validate_key(storage_key: str) -> None:
        """Reject any key not matching the strict allow-list (path traversal, etc.).

        Raises:
            ValidationDomainError: The key is not a well-formed, expected S3 key.
        """
        if "\x00" in storage_key or ".." in storage_key or not _KEY_RE.match(storage_key):
            raise BadRequestError("Invalid storage key.")
