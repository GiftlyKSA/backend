"""Field-encryption key rotation.

Rotation re-encrypts courier identity fields, conversation previews and withdrawal IBANs
using the original AAD. Courier payout IBANs, message bodies and retained operation
results keep their write-time key versions while their ciphertext exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import FieldCipher, blob_version, build_aad, build_cipher
from app.models import Conversation, CourierProfile, Withdrawal


@dataclass(frozen=True)
class RotationReport:
    """Counts for rotated columns and the fields retaining write-time ciphertext."""

    courier_national_id: int
    courier_passport_id: int
    conversation_preview: int
    withdrawal_iban: int
    retained_ciphertext_fields: tuple[str, ...] = field(
        default=(
            "courier_profiles.payout_iban_encrypted",
            "messages.content_encrypted",
            "write_operations.result_encrypted",
        ),
        init=False,
    )

    @property
    def total(self) -> int:
        """Total values re-encrypted across the supported columns."""
        return (
            self.courier_national_id
            + self.courier_passport_id
            + self.conversation_preview
            + self.withdrawal_iban
        )


class KeyRotationService:
    """Re-encrypts supported fields to the active key version."""

    def __init__(self, *, session: AsyncSession, settings: Settings, batch_size: int = 100) -> None:
        """Wire the session and settings (which carry the key map + active version)."""
        self._session = session
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._batch_size = batch_size
        self._settings = settings
        self._active = settings.FIELD_ENCRYPTION_KEY_VERSION
        self._cipher: FieldCipher = build_cipher(
            settings.encryption_keys(), settings.FIELD_ENCRYPTION_KEY_VERSION
        )

    def _reencrypt(self, blob: str, aad: str) -> str | None:
        """Return a re-encrypted blob if it is under an older version, else None."""
        if blob_version(blob) == self._active:
            return None
        plaintext = self._cipher.decrypt(blob, aad)
        return self._cipher.encrypt(plaintext, aad)

    async def rotate(self) -> RotationReport:
        """Rotate supported fields in batches and report retained ciphertext fields."""
        national, passport = await self._rotate_courier_profiles()
        previews = await self._rotate_conversation_previews()
        ibans = await self._rotate_withdrawals()
        return RotationReport(
            courier_national_id=national,
            courier_passport_id=passport,
            conversation_preview=previews,
            withdrawal_iban=ibans,
        )

    async def _rotate_courier_profiles(self) -> tuple[int, int]:
        national = passport = 0
        cursor: UUID | None = None
        while True:
            try:
                query = (
                    select(CourierProfile).order_by(CourierProfile.user_id).limit(self._batch_size)
                )
                if cursor is not None:
                    query = query.where(CourierProfile.user_id > cursor)
                profiles = (await self._session.scalars(query.with_for_update())).all()
                if not profiles:
                    await self._session.commit()
                    break
                batch_national = batch_passport = 0
                for profile in profiles:
                    uid = str(profile.user_id)
                    if profile.national_id_encrypted is not None:
                        new = self._reencrypt(
                            profile.national_id_encrypted,
                            build_aad("courier_profiles", "national_id", uid),
                        )
                        if new is not None:
                            profile.national_id_encrypted = new
                            batch_national += 1
                    if profile.passport_id_encrypted is not None:
                        new = self._reencrypt(
                            profile.passport_id_encrypted,
                            build_aad("courier_profiles", "passport_id", uid),
                        )
                        if new is not None:
                            profile.passport_id_encrypted = new
                            batch_passport += 1
                cursor = profiles[-1].user_id
                await self._session.commit()
                national += batch_national
                passport += batch_passport
            except Exception:
                await self._session.rollback()
                raise
        return national, passport

    async def _rotate_conversation_previews(self) -> int:
        count = 0
        cursor: UUID | None = None
        while True:
            try:
                query = select(Conversation).order_by(Conversation.id).limit(self._batch_size)
                if cursor is not None:
                    query = query.where(Conversation.id > cursor)
                conversations = (await self._session.scalars(query.with_for_update())).all()
                if not conversations:
                    await self._session.commit()
                    break
                batch_count = 0
                for conv in conversations:
                    if conv.last_message_preview_encrypted is not None:
                        new = self._reencrypt(
                            conv.last_message_preview_encrypted,
                            build_aad("conversations", "last_message_preview", str(conv.id)),
                        )
                        if new is not None:
                            conv.last_message_preview_encrypted = new
                            batch_count += 1
                cursor = conversations[-1].id
                await self._session.commit()
                count += batch_count
            except Exception:
                await self._session.rollback()
                raise
        return count

    async def _rotate_withdrawals(self) -> int:
        count = 0
        cursor: UUID | None = None
        while True:
            try:
                query = select(Withdrawal).order_by(Withdrawal.id).limit(self._batch_size)
                if cursor is not None:
                    query = query.where(Withdrawal.id > cursor)
                withdrawals = (await self._session.scalars(query.with_for_update())).all()
                if not withdrawals:
                    await self._session.commit()
                    break
                batch_count = 0
                for wd in withdrawals:
                    new = self._reencrypt(
                        wd.iban_encrypted, build_aad("withdrawals", "iban", str(wd.id))
                    )
                    if new is not None:
                        wd.iban_encrypted = new
                        batch_count += 1
                cursor = withdrawals[-1].id
                await self._session.commit()
                count += batch_count
            except Exception:
                await self._session.rollback()
                raise
        return count
