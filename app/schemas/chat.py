"""Pydantic contracts for the chat endpoints."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class SendMessageRequest(BaseModel):
    """Send a text message into a conversation."""

    model_config = ConfigDict(extra="forbid")
    client_message_id: UUID | None = None
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]


class ChatAttachmentResponse(BaseModel):
    """Attachment display metadata without a reusable public object URL."""

    id: str
    content_type: str
    byte_size: int
    duration_seconds: float | None
    display_order: int


class ChatUploadRequest(BaseModel):
    """Issue one recording/camera/gallery transfer grant for this conversation."""

    model_config = ConfigDict(extra="forbid")
    media_type: Literal["IMAGE", "VIDEO", "VOICE"]
    content_type: str = Field(max_length=32)
    byte_size: int = Field(gt=0, le=125829120)


class SendChatMediaRequest(BaseModel):
    """Send up to five images, one video, or one recorded voice note."""

    model_config = ConfigDict(extra="forbid")
    client_message_id: UUID | None = None
    storage_keys: list[Annotated[str, StringConstraints(max_length=512)]] = Field(
        min_length=1, max_length=5
    )
    text: Annotated[str, StringConstraints(max_length=4000)] = ""


class AttachmentUrlResponse(BaseModel):
    """Fresh short-lived URL after conversation-participant authorization."""

    url: str
    expires_in: int


class ChatMediaLimitsResponse(BaseModel):
    """Configured limits for the chat recorder and camera/gallery selectors."""

    image_max_bytes: int
    video_max_bytes: int
    voice_max_bytes: int
    video_max_duration_seconds: int
    voice_max_duration_seconds: int
    image_content_types: list[str]
    video_content_types: list[str]
    voice_content_types: list[str]
    images_per_message: int = 5
    video_max_pixels: int = 2_073_600
    image_max_pixels: int = 20_000_000


class MessageResponse(BaseModel):
    """A decrypted chat message."""

    id: str
    conversation_id: str
    sender_id: str
    message_type: str
    content: str
    is_read: bool
    created_at: str
    attachments: list[ChatAttachmentResponse] = Field(default_factory=list)


class MessagePage(BaseModel):
    """A keyset page of messages (newest first)."""

    items: list[MessageResponse]
    next_cursor: str | None = None


class ConversationResponse(BaseModel):
    """Conversation identity for an order participant."""

    conversation_id: str
    order_id: str
    other_user_id: str


class InboxItemResponse(BaseModel):
    """A conversation row for the inbox, with a decrypted preview."""

    conversation_id: str
    order_id: str
    other_user_id: str
    last_message_preview: str | None
    unread_count: int
    last_message_timestamp: str


class InboxResponse(BaseModel):
    """A keyset page of conversations."""

    items: list[InboxItemResponse]
    next_cursor: str | None = Field(
        None, description="Opaque `<iso8601>|<uuid>` cursor for the next page."
    )
