"""Authorized order photo metadata and expiring private access."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

MediaPurpose = Literal["ORDER_REQUEST", "DELIVERY_PROOF"]


class OrderMediaResponse(BaseModel):
    """One private photo or video, without exposing its internal object key."""

    id: UUID
    purpose: MediaPurpose
    content_type: str = Field(
        ..., description="Validated original MIME type, including video for delivery proof."
    )
    byte_size: int = Field(..., description="Verified object size in bytes.")
    created_at: datetime
    access_url: str
    expires_at: datetime


class OrderMediaPage(BaseModel):
    """Oldest-created order media first; content_type identifies media format."""

    items: list[OrderMediaResponse]
    next_cursor: str | None
