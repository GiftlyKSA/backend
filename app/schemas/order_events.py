"""Minimal participant-only order status events."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class OrderStatusEvent(BaseModel):
    """Current authoritative state, without customer contact or financial data."""

    type: Literal["order.snapshot", "order.updated"] = "order.snapshot"
    order_id: UUID
    status: str
    courier_id: UUID | None
    assigned_at: datetime | None
