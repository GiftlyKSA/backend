"""Private, conversation-scoped media grants and atomic attachment messages."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    MediaValidationUnavailableError,
    NotFoundError,
)
from app.core.locks import LockNotAcquiredError, redis_lock
from app.integrations.storage.base import StorageClient
from app.models.enums import MessageType
from app.repositories.chat_repository import ChatRepository
from app.repositories.media_repository import MediaRepository
from app.services.chat_media_validation import media_policy, verify_image, verify_recording
from app.services.chat_service import ChatMessage, ChatService, attachment_dto
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.media_service import TransactionBoundary


@dataclass(frozen=True)
class ValidatedAttachment:
    """Trusted snapshot rechecked before the grant is consumed."""

    key: str
    mime: str
    size: int
    duration: Decimal | None = None


class ChatMediaService:
    """Authorize uploads and validate private bytes before committing messages."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        chat: ChatService,
        repository: ChatRepository,
        uploads: MediaRepository,
        storage: StorageClient,
        settings: Settings,
        redis: Redis,
        eligibility: CourierEligibilityService,
    ) -> None:
        """Bind existing persistence, eligibility and private storage boundaries."""
        self._session = session
        self._chat = chat
        self._repository = repository
        self._uploads = uploads
        self._storage = storage
        self._settings = settings
        self._redis = redis
        self._eligibility = eligibility

    async def request_upload(
        self,
        *,
        conversation_id: uuid.UUID,
        actor_id: uuid.UUID,
        kind: str,
        mime: str,
        size: int,
        release_reads: TransactionBoundary | None = None,
        resume_writes: TransactionBoundary | None = None,
    ) -> tuple[str, str, int]:
        """Issue an immutable, size-bound grant only to a conversation participant."""
        await self._chat.get_conversation_for_actor(
            conversation_id=conversation_id,
            actor_id=actor_id,
        )
        extension, _ = media_policy(self._settings, kind, mime, size)
        key = f"chat/{conversation_id}/{uuid.uuid4()}.{extension}"
        if release_reads is not None:
            await release_reads()
        url = await self._storage.create_upload_url(
            storage_key=key,
            content_type=mime,
            byte_size=size,
            ttl_seconds=300,
        )
        if resume_writes is not None:
            await resume_writes()
            await self._chat.get_conversation_for_actor(
                conversation_id=conversation_id,
                actor_id=actor_id,
            )
        await self._uploads.issue(
            storage_key=key,
            actor_id=actor_id,
            purpose="CHAT_ATTACHMENT",
            content_type=mime,
            byte_size=size,
            max_count=20,
            max_bytes=250 * 1024 * 1024,
        )
        return url, key, 300

    async def _grant(
        self,
        key: str,
        conversation_id: uuid.UUID,
        actor_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> ValidatedAttachment:
        grant = await self._uploads.get(key, for_update=for_update)
        if (
            grant is None
            or grant.owner_user_id != actor_id
            or grant.purpose != "CHAT_ATTACHMENT"
            or not key.startswith(f"chat/{conversation_id}/")
        ):
            raise BadRequestError("The upload is not available for this conversation.")
        if grant.attached_at is not None or grant.deleting_at is not None:
            raise ConflictError("This upload has already been used or expired.")
        return ValidatedAttachment(key, grant.content_type, grant.byte_size)

    @staticmethod
    def _kind(mime: str) -> str:
        return {"image": "IMAGE", "video": "VIDEO", "audio": "VOICE"}.get(
            mime.partition("/")[0],
            "",
        )

    async def prepare(
        self,
        *,
        conversation_id: uuid.UUID,
        actor_id: uuid.UUID,
        keys: list[str],
    ) -> list[ValidatedAttachment]:
        """Release the read transaction before bounded S3 reads and media decoding."""
        if not 1 <= len(keys) <= 5 or len(keys) != len(set(keys)):
            raise BadRequestError("Provide one to five unique uploads.")
        await self._chat.get_conversation_for_actor(
            conversation_id=conversation_id,
            actor_id=actor_id,
        )
        grants = [await self._grant(key, conversation_id, actor_id) for key in keys]
        kinds = {self._kind(grant.mime) for grant in grants}
        if len(kinds) != 1 or (kinds != {"IMAGE"} and len(grants) != 1):
            raise BadRequestError("Send up to five images, one video, or one voice note.")
        await self._session.commit()
        async with AsyncExitStack() as stack:
            for slot in range(2):
                try:
                    await stack.enter_async_context(
                        redis_lock(
                            self._redis,
                            f"lock:chat-media-validation:{slot}",
                            ttl_seconds=60,
                        )
                    )
                    break
                except LockNotAcquiredError:
                    continue
            else:
                raise MediaValidationUnavailableError()
            try:
                async with asyncio.timeout(45):
                    return [await self._verify(grant) for grant in grants]
            except TimeoutError as exc:
                raise MediaValidationUnavailableError() from exc

    async def _verify(self, grant: ValidatedAttachment) -> ValidatedAttachment:
        kind = self._kind(grant.mime)
        _, maximum = media_policy(self._settings, kind, grant.mime, grant.size)
        head = await self._storage.head_object(grant.key)
        if (
            head is None
            or not head.exists
            or head.byte_size != grant.size
            or head.content_type != grant.mime
        ):
            raise BadRequestError("The uploaded file does not match its upload grant.")
        try:
            body = await self._storage.read_bounded_object(grant.key, max_bytes=grant.size)
        except ValueError as exc:
            raise BadRequestError("The uploaded file exceeds its declared size.") from exc
        except NotImplementedError as exc:
            raise MediaValidationUnavailableError() from exc
        if len(body) != grant.size:
            raise BadRequestError("The uploaded file size does not match its upload grant.")
        if kind == "IMAGE":
            await asyncio.to_thread(verify_image, body, grant.mime)
            return grant
        duration = await verify_recording(body, kind=kind, mime=grant.mime, maximum=maximum)
        return replace(
            grant,
            duration=Decimal(str(duration)).quantize(Decimal("0.001"), rounding=ROUND_CEILING),
        )

    async def send(
        self,
        *,
        conversation_id: uuid.UUID,
        actor_id: uuid.UUID,
        attachments: list[ValidatedAttachment],
        text: str,
    ) -> ChatMessage:
        """Recheck grants and atomically save a message with all attachments."""
        dto = await self._chat.send_message(
            conversation_id=conversation_id,
            sender_id=actor_id,
            text=text,
            message_type=MessageType(self._kind(attachments[0].mime)),
        )
        for attachment in sorted(attachments, key=lambda item: item.key):
            current = await self._grant(attachment.key, conversation_id, actor_id, for_update=True)
            if (current.mime, current.size) != (attachment.mime, attachment.size):
                raise ConflictError("The upload changed during validation.")
            if not await self._uploads.mark_confirmed(attachment.key, actor_id):
                raise ConflictError("The upload is no longer available.")
            if not await self._uploads.claim(attachment.key, actor_id, "CHAT_ATTACHMENT"):
                raise ConflictError("The upload has already been used.")
        saved = []
        for position, attachment in enumerate(attachments):
            record = await self._repository.add_attachment(
                message_id=uuid.UUID(dto.id),
                actor_id=actor_id,
                storage_key=attachment.key,
                content_type=attachment.mime,
                byte_size=attachment.size,
                display_order=position,
                duration_seconds=attachment.duration,
            )
            if record is None:
                raise NotFoundError("Conversation not found.")
            saved.append(attachment_dto(record))
        return replace(dto, attachments=saved)

    async def playback(self, *, attachment_id: uuid.UUID, actor_id: uuid.UUID) -> str:
        """Provide short-lived private access only to a current eligible participant."""
        await self._eligibility.require_eligible_actor(actor_id)
        attachment = await self._repository.attachment_for_actor(attachment_id, actor_id)
        if attachment is None:
            raise NotFoundError("Attachment not found.")
        return await asyncio.to_thread(
            self._storage.signed_read_url, attachment.storage_key, ttl_seconds=300
        )
