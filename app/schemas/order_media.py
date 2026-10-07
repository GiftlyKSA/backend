"""Authorized order photo metadata and expiring private access."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

MediaPurpose = Literal["ORDER_REQUEST", "DELIVERY_PROOF"]


class OrderMediaResponse(BaseModel):
    """One photo, without exposing its internal object key."""

    id: UUID
    purpose: MediaPurpose
    content_type: str
    byte_size: int
    created_at: datetime
    access_url: str
    expires_at: datetime


class OrderMediaPage(BaseModel):
    """Oldest-created order media first."""

    items: list[OrderMediaResponse]
    next_cursor: str | None
