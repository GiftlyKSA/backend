"""Dedicated order history without internal audit metadata."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import OrderStatus


class TimelineActor(BaseModel):
    """Only the role that initiated a change, if known."""

    role: Literal["CUSTOMER", "COURIER", "ADMIN"]


class TimelineEventResponse(BaseModel):
    """One real status event."""

    id: UUID
    event_type: Literal["STATUS_CHANGED"] = "STATUS_CHANGED"
    status: OrderStatus
    created_at: datetime
    actor: TimelineActor | None = None


class OrderTimelinePage(BaseModel):
    """Oldest-first events; historical orders have explicitly incomplete history."""

    items: list[TimelineEventResponse]
    next_cursor: str | None = None
    history_complete: bool = Field(description="False for orders predating status event capture.")
