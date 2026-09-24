"""Authorized, audited table maintenance for the admin dashboard."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.crypto import build_aad, build_cipher
from app.core.exceptions import ConflictError, ForbiddenError
from app.core.identity import identity_fingerprint
from app.core.security import hmac_hex
from app.models.enums import UserRole, UserStatus
from app.repositories.admin_table_repository import AdminTableRepository, get_table, primary_key
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_repository import AuthRepository
from app.services.admin_table_fields import TableField, form_fields, parse_values


@dataclass(frozen=True)
class TableForm:
    """A complete, redacted add/edit form."""

    table_name: str
    fields: list[TableField]
    record_id: uuid.UUID | None
    revision: str


class AdminTableService:
    """Keep validation, authorization, encryption, and audit rules out of routes."""

    def __init__(
        self,
        repository: AdminTableRepository,
        audit: AuditRepository,
        settings: Settings,
        auth: AuthRepository,
    ) -> None:
        """Bind collaborators to the same request transaction."""
        self._repo = repository
        self._audit = audit
        self._settings = settings
        self._auth = auth

    async def form(self, table_name: str, record_id: uuid.UUID | None = None) -> TableForm:
        """Load one form and its selected relationship labels in bounded queries."""
        table = get_table(table_name)
        row = await self._repo.get_record(table_name, record_id) if record_id else None
        labels = await self._repo.selected_labels(table, row) if row else {}
        fields = [
            replace(field, selected_label=labels.get(field.name, ""))
            for field in form_fields(table, row)
        ]
        return TableForm(table_name, fields, record_id, self._revision(row))

    def _revision(self, row: dict[str, Any] | None) -> str:
        if row is None:
            return ""
        secret = self._settings.ADMIN_SESSION_SECRET
        if secret is None:
            raise ForbiddenError("Admin dashboard is not configured.")
        return hmac_hex(json.dumps(row, sort_keys=True, default=str), secret.get_secret_value())

    async def _authorize(self, admin_id: uuid.UUID) -> None:
        admin = await self._repo.get_record("users", admin_id)
        if (
            admin["role"] != UserRole.ADMIN
            or admin["status"] != UserStatus.ACTIVE
            or admin["deleted_at"] is not None
        ):
            raise ForbiddenError("An active administrator account is required.")

    async def save(
        self,
        table_name: str,
        submitted: dict[str, str],
        *,
        admin_id: uuid.UUID,
        session_id: uuid.UUID,
        record_id: uuid.UUID | None = None,
        revision: str = "",
        ip: str | None = None,
    ) -> uuid.UUID:
        """Apply explicitly authorized table edits atomically with their audit record."""
        await self._authorize(admin_id)
        table = get_table(table_name)
        creating = record_id is None
        values = parse_values(table, submitted, creating=creating)
        key = primary_key(table)
        if record_id is None:
            record_id = values.get(key.name, uuid.uuid4())
        assert record_id is not None
        async with self._repo.transaction():
            await self._repo.authorize_maintenance(admin_id, session_id)
            old = (
                None if creating else await self._repo.get_record(table_name, record_id, lock=True)
            )
            if old is not None and self._revision(old) != revision:
                raise ConflictError("This record changed. Reload it before saving.")
            self._encrypt(table_name, record_id, values, old)
            await self._repo.save(table, record_id, values, creating=creating)
            if (
                table_name == "users"
                and old is not None
                and any(
                    field in values and values[field] != old[field]
                    for field in ("phone", "role", "status", "deleted_at")
                )
            ):
                await self._auth.invalidate_user_credentials(record_id, datetime.now(UTC))
            await self._audit.record(
                actor_user_id=admin_id,
                action="ADMIN_TABLE_CREATE" if creating else "ADMIN_TABLE_UPDATE",
                entity_type=table_name,
                entity_id=record_id,
                ip_address=ip,
                metadata={"fields": sorted(values)},
            )
            await self._repo.clear_maintenance()
        return record_id

    async def delete(
        self,
        table_name: str,
        record_id: uuid.UUID,
        *,
        admin_id: uuid.UUID,
        session_id: uuid.UUID,
        revision: str,
        ip: str | None = None,
    ) -> None:
        """Delete one record and audit the operation in the same transaction."""
        await self._authorize(admin_id)
        table = get_table(table_name)
        async with self._repo.transaction():
            await self._repo.authorize_maintenance(admin_id, session_id)
            old = await self._repo.get_record(table_name, record_id, lock=True)
            if self._revision(old) != revision:
                raise ConflictError("This record changed. Reload it before deleting.")
            await self._repo.remove(table, record_id)
            await self._audit.record(
                actor_user_id=admin_id,
                action="ADMIN_TABLE_DELETE",
                entity_type=table_name,
                entity_id=record_id,
                ip_address=ip,
            )
            await self._repo.clear_maintenance()

    def _encrypt(
        self,
        table: str,
        record_id: uuid.UUID,
        values: dict[str, Any],
        old: dict[str, Any] | None,
    ) -> None:
        cipher = build_cipher(
            self._settings.encryption_keys(), self._settings.FIELD_ENCRYPTION_KEY_VERSION
        )
        if table == "courier_profiles":
            self._derive_identity(record_id, values, old)
        combined = {**(old or {}), **values}
        if table == "messages" and old and combined["conversation_id"] != old["conversation_id"]:
            if "content_encrypted" not in values:
                values["content_encrypted"] = cipher.decrypt(
                    old["content_encrypted"],
                    build_aad(table, "content", str(old["conversation_id"])),
                )
        for name in list(values):
            if not name.endswith("_encrypted") or values[name] is None:
                continue
            plaintext = str(values[name])
            owner = combined["conversation_id"] if table == "messages" else record_id
            values[name] = cipher.encrypt(
                plaintext, build_aad(table, name.removesuffix("_encrypted"), str(owner))
            )
            if table == "withdrawals" and name == "iban_encrypted":
                values["iban_last4"] = plaintext[-4:]

    def _derive_identity(
        self, record_id: uuid.UUID, values: dict[str, Any], old: dict[str, Any] | None
    ) -> None:
        fields = ("national_id_encrypted", "passport_id_encrypted")
        if old is not None and not any(field in values for field in fields):
            return
        cipher = build_cipher(
            self._settings.encryption_keys(), self._settings.FIELD_ENCRYPTION_KEY_VERSION
        )
        documents: list[str | None] = []
        for field in fields:
            if field in values:
                values[field] = str(values[field]).strip() or None if values[field] else None
                documents.append(values[field])
            elif old and old.get(field):
                documents.append(
                    cipher.decrypt(
                        old[field],
                        build_aad(
                            "courier_profiles", field.removesuffix("_encrypted"), str(record_id)
                        ),
                    )
                )
            else:
                documents.append(None)
        fingerprint = identity_fingerprint(
            documents[0],
            documents[1],
            self._settings.IDENTITY_FINGERPRINT_PEPPER.get_secret_value(),
        )
        # Explicit raw maintenance overrides remain available to authorized admins.
        values.setdefault("identity_fingerprint", fingerprint)
