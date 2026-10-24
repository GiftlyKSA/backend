"""Customer-facing promo catalog contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import PromoDiscountType


class PromoCatalogItem(BaseModel):
    """A currently active promo, without its internal database identifier."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    discount_type: PromoDiscountType
    percent_value: str | None
    fixed_amount: str | None
    max_discount_amount: str | None
    min_order_amount: str
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime


class PromoCatalogResponse(BaseModel):
    """A bounded page of currently active promo codes."""

    items: list[PromoCatalogItem]
