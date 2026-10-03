"""Chat service: encrypted messaging over an order's conversation (SPEC SECTION 10, 20).

Message content and the inbox preview are AES-256-GCM encrypted at rest, bound by AAD to
their row and column. Plaintext never lands in an unencrypted column (ADR 0004). On send
the service publishes the (decrypted) message to a Redis channel so every connected
WebSocket for that conversation — on any instance — receives it in real time.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.crypto import FieldCipher, build_aad, build_cipher
from app.core.exceptions import NotFoundError
from app.models import Conversation, Message, MessageAttachment
from app.models.enums import MessageType
from app.repositories.chat_repository import ChatRepository
from app.services.courier_eligibility_service import CourierEligibilityService

_PREVIEW_CHARS = 100


def conversation_channel(conversation_id: uuid.UUID) -> str:
    """The Redis pub/sub channel that carries a conversation's live messages."""
    return f"chat:conversation:{conversation_id}"


@dataclass(frozen=True)
class ChatAttachment:
    """Private attachment metadata; playback requires a separate authorized request."""

    id: str
    content_type: str
    byte_size: int
    duration_seconds: float | None
    display_order: int


def attachment_dto(attachment: MessageAttachment) -> ChatAttachment:
    """Expose display metadata without granting public storage access."""
    return ChatAttachment(
        str(attachment.id),
        attachment.content_type,
        attachment.byte_size,
        float(attachment.duration_seconds) if attachment.duration_seconds is not None else None,
        attachment.display_order,
    )


@dataclass(frozen=True)
class ChatMessage:
    """A decrypted message for the API/WS layer."""

    id: str
    conversation_id: str
    sender_id: str
    message_type: str
    content: str
    is_read: bool
    created_at: str
    attachments: list[ChatAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class InboxItem:
    """A decrypted inbox row."""

    conversation_id: str
    order_id: str
    other_user_id: str
    last_message_preview: str | None
    unread_count: int
    last_message_timestamp: str


class ChatService:
    """Sends, lists, and marks-read encrypted chat messages."""

    def __init__(
        self,
        *,
        chat: ChatRepository,
        redis: Redis,
        settings: Settings,
        eligibility: CourierEligibilityService,
    ) -> None:
        """Wire the chat repository, Redis (for WS fanout), and settings."""
        self._chat = chat
        self._redis = redis
        self._settings = settings
        self._eligibility = eligibility

    def _cipher(self) -> FieldCipher:
        return build_cipher(
            self._settings.encryption_keys(), self._settings.FIELD_ENCRYPTION_KEY_VERSION
        )

    def _encrypt_content(self, conversation_id: uuid.UUID, text: str) -> str:
        aad = build_aad("messages", "content", str(conversation_id))
        return self._cipher().encrypt(text, aad)

    def _decrypt_content(self, conversation_id: uuid.UUID, blob: str) -> str:
        aad = build_aad("messages", "content", str(conversation_id))
        return self._cipher().decrypt(blob, aad)

    def _encrypt_preview(self, conversation_id: uuid.UUID, text: str) -> str:
        aad = build_aad("conversations", "last_message_preview", str(conversation_id))
        return self._cipher().encrypt(text[:_PREVIEW_CHARS], aad)

    def _decrypt_preview(self, conversation_id: uuid.UUID, blob: str) -> str:
        aad = build_aad("conversations", "last_message_preview", str(conversation_id))
        return self._cipher().decrypt(blob, aad)

    async def _require_conversation(
        self, conversation_id: uuid.UUID, actor_id: uuid.UUID, *, for_update: bool = False
    ) -> Conversation:
        return await self.get_conversation_for_actor(
            conversation_id=conversation_id, actor_id=actor_id, for_update=for_update
        )

    async def get_conversation_for_actor(
        self, *, conversation_id: uuid.UUID, actor_id: uuid.UUID, for_update: bool = False
    ) -> Conversation:
        """Return an eligible actor's conversation or hide it as not found."""
        await self._eligibility.require_eligible_actor(actor_id)
        if for_update:
            conversation = await self._chat.get_for_actor(
                conversation_id, actor_id, for_update=True
            )
        else:
            conversation = await self._chat.get_for_actor(conversation_id, actor_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    async def get_conversation_for_order(
        self, *, order_id: uuid.UUID, actor_id: uuid.UUID
    ) -> Conversation:
        """Return an order's conversation to its eligible customer or courier."""
        await self._eligibility.require_eligible_actor(actor_id)
        conversation = await self._chat.get_for_order_and_actor(order_id, actor_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    async def send_message(
        self,
        *,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID,
        text: str,
        message_type: MessageType = MessageType.TEXT,
    ) -> ChatMessage:
        """Encrypt and append a message for its caller to commit.

        Raises:
            NotFoundError: The sender does not participate in the conversation.
        """
        conversation = await self._require_conversation(conversation_id, sender_id, for_update=True)
        content = self._encrypt_content(conversation_id, text)
        preview_text = text or {
            MessageType.IMAGE: "Image",
            MessageType.VIDEO: "Video",
            MessageType.VOICE: "Voice note",
        }.get(message_type, "")
        preview = self._encrypt_preview(conversation_id, preview_text)
        message = await self._chat.add_message(
            conversation=conversation,
            sender_id=sender_id,
            content_encrypted=content,
            preview_encrypted=preview,
            sender_is_customer=sender_id == conversation.customer_id,
            message_type=message_type,
        )
        return self._to_dto(message, text)

    async def publish_message(self, message: ChatMessage) -> None:
        """Publish a committed message for live delivery."""
        await self._publish(uuid.UUID(message.conversation_id), message)

    async def list_messages(
        self,
        *,
        conversation_id: uuid.UUID,
        actor_id: uuid.UUID,
        limit: int,
        before_id: uuid.UUID | None,
    ) -> list[ChatMessage]:
        """Return decrypted messages for a participant, newest first."""
        await self._require_conversation(conversation_id, actor_id)
        rows = await self._chat.list_messages(conversation_id, limit=limit, before_id=before_id)
        attachments = await self._chat.attachments_for_messages([m.id for m in rows])
        by_message: dict[uuid.UUID, list[ChatAttachment]] = {}
        for attachment in attachments:
            by_message.setdefault(attachment.message_id, []).append(attachment_dto(attachment))
        return [
            replace(
                self._to_dto(m, self._decrypt_content(conversation_id, m.content_encrypted)),
                attachments=by_message.get(m.id, []),
            )
            for m in rows
        ]

    async def mark_read(self, *, conversation_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        """Mark a participant's inbound messages read and clear their unread count."""
        conversation = await self._require_conversation(conversation_id, actor_id, for_update=True)
        await self._chat.mark_read(
            conversation, reader_is_customer=actor_id == conversation.customer_id
        )

    async def list_inbox(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
        before: tuple[datetime, uuid.UUID] | None,
    ) -> list[InboxItem]:
        """Return a user's conversations with decrypted previews and unread counts."""
        await self._eligibility.require_eligible_actor(user_id)
        conversations = await self._chat.list_for_user(user_id, limit=limit, before=before)
        items: list[InboxItem] = []
        for conv in conversations:
            is_customer = conv.customer_id == user_id
            preview = None
            if conv.last_message_preview_encrypted is not None:
                preview = self._decrypt_preview(conv.id, conv.last_message_preview_encrypted)
            items.append(
                InboxItem(
                    conversation_id=str(conv.id),
                    order_id=str(conv.order_id),
                    other_user_id=str(conv.courier_id if is_customer else conv.customer_id),
                    last_message_preview=preview,
                    unread_count=(
                        conv.customer_unread_count if is_customer else conv.courier_unread_count
                    ),
                    last_message_timestamp=conv.last_message_timestamp.isoformat(),
                )
            )
        return items

    async def _publish(self, conversation_id: uuid.UUID, message: ChatMessage) -> None:
        payload = json.dumps(
            {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "sender_id": message.sender_id,
                "message_type": message.message_type,
                "content": message.content,
                "is_read": message.is_read,
                "created_at": message.created_at,
                "attachments": [asdict(a) for a in message.attachments],
            }
        )
        await self._redis.publish(conversation_channel(conversation_id), payload)

    @staticmethod
    def _to_dto(message: Message, content: str) -> ChatMessage:
        return ChatMessage(
            id=str(message.id),
            conversation_id=str(message.conversation_id),
            sender_id=str(message.sender_id),
            message_type=str(message.message_type),
            content=content,
            is_read=message.is_read,
            created_at=message.created_at.isoformat(),
        )
