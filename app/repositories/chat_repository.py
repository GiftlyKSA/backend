"""Chat persistence: conversations and messages (SPEC SECTION 10, 20, ADR 0004).

``messages`` is append-only (a trigger forbids UPDATE/DELETE except is_read/read_at) and
``content_encrypted`` is AES-256-GCM. The inbox preview is stored ENCRYPTED in
``conversations.last_message_preview_encrypted`` (ADR 0004), decrypted one row at a time.
Ownership is enforced in the query — a conversation is returned only to its two members.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert, literal, select, tuple_, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import (
    ChatNotification,
    Conversation,
    CourierProfile,
    Message,
    MessageAttachment,
    User,
)
from app.models.enums import MessageType
from app.repositories.user_repository import AccountState


@dataclass(frozen=True)
class LiveChatState:
    """Current account and owned conversation fields from one database statement."""

    user: AccountState
    courier_verified: bool | None
    conversation_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    courier_id: uuid.UUID | None


@dataclass(frozen=True)
class AttachmentInput:
    """Validated metadata to insert in caller-specified display order."""

    storage_key: str
    content_type: str
    byte_size: int
    display_order: int
    duration_seconds: Decimal | None


class ChatRepository:
    """Reads conversations and appends/reads/marks-read chat messages."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def get_live_state(
        self, conversation_id: uuid.UUID, actor_id: uuid.UUID
    ) -> LiveChatState | None:
        """Select current credentials, verification and membership without ORM loaders."""
        row = (
            await self._session.execute(
                select(
                    User.id,
                    User.role,
                    User.status,
                    User.deleted_at,
                    User.auth_version,
                    CourierProfile.is_verified,
                    Conversation.id,
                    Conversation.customer_id,
                    Conversation.courier_id,
                )
                .outerjoin(CourierProfile, CourierProfile.user_id == User.id)
                .outerjoin(
                    Conversation,
                    (Conversation.id == conversation_id)
                    & (
                        (Conversation.customer_id == actor_id)
                        | (Conversation.courier_id == actor_id)
                    ),
                )
                .where(User.id == actor_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return LiveChatState(AccountState(*row[:5]), *row[5:])

    async def get_for_actor(
        self, conversation_id: uuid.UUID, actor_id: uuid.UUID, *, for_update: bool = False
    ) -> Conversation | None:
        """Return a conversation only if the actor is its customer or courier."""
        query = select(Conversation).where(
            Conversation.id == conversation_id,
            (Conversation.customer_id == actor_id) | (Conversation.courier_id == actor_id),
        )
        if for_update:
            query = query.with_for_update().execution_options(populate_existing=True)
        result: Conversation | None = await self._session.scalar(query)
        return result

    async def get_for_order_and_actor(
        self, order_id: uuid.UUID, actor_id: uuid.UUID
    ) -> Conversation | None:
        """Look up an order's conversation within the caller's participant scope."""
        result: Conversation | None = await self._session.scalar(
            select(Conversation).where(
                Conversation.order_id == order_id,
                (Conversation.customer_id == actor_id) | (Conversation.courier_id == actor_id),
            )
        )
        return result

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int, before: tuple[datetime, uuid.UUID] | None
    ) -> list[Conversation]:
        """Return a user's conversations, most-recent activity first (keyset paged)."""
        query = select(Conversation).where(
            (Conversation.customer_id == user_id) | (Conversation.courier_id == user_id)
        )
        query = query.order_by(
            Conversation.last_message_timestamp.desc(), Conversation.id.desc()
        ).limit(limit)
        if before is not None:
            query = query.where(
                tuple_(Conversation.last_message_timestamp, Conversation.id) < before
            )
        return list(await self._session.scalars(query))

    async def add_message(
        self,
        *,
        conversation: Conversation,
        sender_id: uuid.UUID,
        content_encrypted: str,
        preview_encrypted: str,
        sender_is_customer: bool,
        message_type: MessageType = MessageType.TEXT,
    ) -> Message:
        """Append a message and update the conversation preview, timestamp, and unread.

        The recipient's unread counter is bumped (the sender's is untouched); the
        encrypted preview and last-message timestamp are refreshed.
        """
        message = Message(
            conversation_id=conversation.id,
            sender_id=sender_id,
            message_type=message_type,
            content_encrypted=content_encrypted,
        )
        self._session.add(message)
        await self._session.flush()
        self._session.add(
            ChatNotification(
                message_id=message.id,
                recipient_id=(
                    conversation.courier_id if sender_is_customer else conversation.customer_id
                ),
            )
        )

        conversation.last_message_preview_encrypted = preview_encrypted
        conversation.last_message_timestamp = message.created_at or datetime.now(UTC)
        if sender_is_customer:
            conversation.courier_unread_count += 1
        else:
            conversation.customer_unread_count += 1
        await self._session.flush()
        return message

    async def add_attachment(
        self,
        *,
        message_id: uuid.UUID,
        actor_id: uuid.UUID,
        storage_key: str,
        content_type: str,
        byte_size: int,
        display_order: int,
        duration_seconds: Decimal | None = None,
    ) -> MessageAttachment | None:
        """Attach media only to a message sent by this conversation participant."""
        authorized_values = (
            select(
                literal(message_id),
                literal(storage_key),
                literal(content_type),
                literal(byte_size),
                literal(display_order),
                literal(duration_seconds),
            )
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.id == message_id,
                Message.sender_id == actor_id,
                (Conversation.customer_id == actor_id) | (Conversation.courier_id == actor_id),
            )
        )
        statement = (
            insert(MessageAttachment)
            .from_select(
                [
                    "message_id",
                    "storage_key",
                    "content_type",
                    "byte_size",
                    "display_order",
                    "duration_seconds",
                ],
                authorized_values,
            )
            .returning(MessageAttachment)
        )
        attachment = await self._session.scalar(statement)
        await self._session.flush()
        return attachment

    async def attachments_for_messages(self, ids: list[uuid.UUID]) -> list[MessageAttachment]:
        """Load attachments in one query after the service authorizes the conversation."""
        if not ids:
            return []
        return list(
            await self._session.scalars(
                select(MessageAttachment)
                .where(MessageAttachment.message_id.in_(ids))
                .order_by(MessageAttachment.message_id, MessageAttachment.display_order)
            )
        )

    async def add_attachments(
        self, *, message_id: uuid.UUID, actor_id: uuid.UUID, attachments: list[AttachmentInput]
    ) -> list[MessageAttachment]:
        """Insert a bounded batch only for its sender and current conversation member."""
        if not attachments:
            return []
        columns = ("storage_key", "content_type", "byte_size", "display_order", "duration_seconds")
        payload = union_all(
            *(
                select(
                    *(
                        literal(
                            getattr(attachment, name),
                            type_=MessageAttachment.__table__.c[name].type,
                        ).label(name)
                        for name in columns
                    )
                )
                for attachment in attachments
            )
        ).subquery()
        authorized_values = (
            select(Message.id, *(payload.c[name] for name in columns))
            .select_from(payload)
            .join(Message, Message.id == message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.sender_id == actor_id,
                (Conversation.customer_id == actor_id) | (Conversation.courier_id == actor_id),
            )
        )
        records = list(
            await self._session.scalars(
                insert(MessageAttachment)
                .from_select(["message_id", *columns], authorized_values)
                .returning(MessageAttachment)
            )
        )
        return sorted(records, key=lambda row: row.display_order)

    async def attachment_for_actor(
        self, attachment_id: uuid.UUID, actor_id: uuid.UUID
    ) -> MessageAttachment | None:
        """Hide absent or foreign attachments with the same scoped lookup."""
        result: MessageAttachment | None = await self._session.scalar(
            select(MessageAttachment)
            .join(Message, Message.id == MessageAttachment.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                MessageAttachment.id == attachment_id,
                (Conversation.customer_id == actor_id) | (Conversation.courier_id == actor_id),
            )
        )
        return result

    async def list_attachments_for_actor(
        self, message_id: uuid.UUID, actor_id: uuid.UUID
    ) -> list[MessageAttachment]:
        """Return message attachments only to a participant in its conversation."""
        return list(
            await self._session.scalars(
                select(MessageAttachment)
                .join(Message, Message.id == MessageAttachment.message_id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    MessageAttachment.message_id == message_id,
                    (Conversation.customer_id == actor_id) | (Conversation.courier_id == actor_id),
                )
                .order_by(MessageAttachment.display_order, MessageAttachment.id)
            )
        )

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int,
        before_id: uuid.UUID | None,
    ) -> list[Message]:
        """Return a conversation's messages, newest first, keyset-paged."""
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            anchor = await self._session.scalar(
                select(Message).where(
                    Message.id == before_id, Message.conversation_id == conversation_id
                )
            )
            if anchor is None:
                raise NotFoundError("Message cursor not found.")
            query = query.where(
                tuple_(Message.created_at, Message.id) < (anchor.created_at, anchor.id)
            )
        return list(await self._session.scalars(query))

    async def mark_read(self, conversation: Conversation, *, reader_is_customer: bool) -> None:
        """Reset the reader's unread counter and mark inbound messages read.

        Only is_read/read_at change on the messages — the append-only trigger permits it.
        """
        other_id = conversation.courier_id if reader_is_customer else conversation.customer_id
        if reader_is_customer:
            conversation.customer_unread_count = 0
        else:
            conversation.courier_unread_count = 0
        await self._session.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.sender_id == other_id,
                Message.is_read.is_(False),
            )
            .values(is_read=True, read_at=datetime.now(UTC))
        )
        await self._session.flush()
