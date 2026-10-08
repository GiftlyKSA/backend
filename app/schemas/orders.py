"""Pydantic contracts for the order endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


class CreateOrderRequest(BaseModel):
    """Create a gift-request order."""

    model_config = ConfigDict(extra="forbid")
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    delivery_city: Annotated[str, StringConstraints(max_length=100)] | None = Field(
        None, description="Legacy city name; select delivery_city_id from GET /api/cities."
    )
    delivery_city_id: UUID | None = None
    delivery_date: date = Field(..., description="Requested delivery date (<= 6 months out).")
    request_media_keys: list[str] = Field(
        default_factory=list, max_length=3, description="Confirmed request-photo keys (0–3)."
    )

    @model_validator(mode="after")
    def validate_city_choice(self) -> CreateOrderRequest:
        """Require one city selection without ambiguous dual inputs."""
        if (self.delivery_city is None) == (self.delivery_city_id is None):
            raise ValueError("Provide exactly one city name or city ID.")
        return self


class CancelOrderRequest(BaseModel):
    """Cancel an order before it is in progress."""

    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, StringConstraints(max_length=255)] | None = None


class OrderSummary(BaseModel):
    """A compact order row for lists and the radar."""

    id: str
    status: str
    delivery_city: str
    delivery_city_id: UUID
    delivery_date: str
    description: str | None
    created_at: str
    current_actor_has_rated: bool = Field(
        ..., description="Whether the authenticated actor has rated this order."
    )


class OrderDetail(BaseModel):
    """A full order view for its participants."""

    id: str
    status: str
    customer_id: str
    courier_id: str | None
    delivery_city: str
    delivery_city_id: UUID
    delivery_date: str
    description: str | None
    total_amount: str
    assigned_at: str | None
    created_at: str
    current_actor_has_rated: bool = Field(
        ..., description="Whether the authenticated actor has rated this order."
    )


class OrderListResponse(BaseModel):
    """A keyset page of order summaries."""

    items: list[OrderSummary]
    next_cursor: str | None = None
