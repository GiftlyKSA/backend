"""Owned, encrypted operation recovery without treating timeouts as failed writes."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.core.config import Settings
from app.core.crypto import build_aad, build_cipher
from app.core.exceptions import ConflictError, DomainError, NotFoundError
from app.models import WriteOperation
from app.repositories.operation_repository import OperationRepository


class OperationPendingError(DomainError):
    """A committed operation has an unresolved external outcome."""

    code = "OPERATION_PENDING"
    message = "This operation is unresolved. Recover the existing result before retrying."
    status_code = 503


class OperationService:
    """Bind replay identity, ownership and encrypted snapshots."""

    def __init__(self, repo: OperationRepository, settings: Settings) -> None:
        """Bind persistence and the versioned encryption keyring."""
        self._repo = repo
        self._cipher = build_cipher(
            settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION
        )

    @staticmethod
    def fingerprint(payload: dict[str, JsonValue]) -> str:
        """Hash canonical input while preserving list order."""
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    async def find(
        self, owner: UUID, operation: str, key: UUID, payload: dict[str, JsonValue] | None = None
    ) -> WriteOperation | None:
        """Read a retained result only while its resource remains accessible."""
        row = await self._repo.get(owner, operation, key)
        if row is None:
            return None
        if row.result_encrypted is not None and row.expires_at <= datetime.now(UTC):
            raise NotFoundError("No retained operation result. An expired key must not be reused.")
        if payload is not None and row.request_hash != self.fingerprint(payload):
            raise ConflictError("This operation key was already used with different input.")
        if not await self._repo.resource_accessible(row):
            raise NotFoundError()
        return row

    async def begin(
        self, owner: UUID, operation: str, key: UUID, payload: dict[str, JsonValue]
    ) -> WriteOperation:
        """Claim a key atomically or validate an existing replay."""
        fingerprint = self.fingerprint(payload)
        row, created = await self._repo.claim(
            owner, operation, key, fingerprint, datetime.now(UTC) + timedelta(hours=24)
        )
        if created:
            row.request_hash = fingerprint
        else:
            if row.request_hash != fingerprint:
                raise ConflictError("This operation key was already used with different input.")
            if row.result_encrypted is None:
                raise OperationPendingError()
            if row.expires_at <= datetime.now(UTC):
                raise ConflictError("This operation key has expired and must not be reused.")
            if not await self._repo.resource_accessible(row):
                raise NotFoundError()
        return row

    def result(self, row: WriteOperation) -> str:
        """Decrypt the original response without inferring payment success."""
        if row.result_encrypted is None:
            raise OperationPendingError()
        return self._cipher.decrypt(
            row.result_encrypted, build_aad("write_operations", "result", str(row.id))
        )

    async def bind(self, row: WriteOperation, resource_id: UUID) -> None:
        """Persist recovery linkage before an external checkout can begin."""
        row.resource_id = resource_id
        await self._repo.save(row)

    async def finish(self, row: WriteOperation, response: BaseModel, resource_id: UUID) -> None:
        """Commit an encrypted response with its resource mutation."""
        row.resource_id = resource_id
        row.result_encrypted = self._cipher.encrypt(
            response.model_dump_json(),
            build_aad("write_operations", "result", str(row.id)),
        )
        await self._repo.save(row)
