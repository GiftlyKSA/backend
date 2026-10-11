"""Typed localizable inbox events; no free-form personal/provider payloads."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OrderStatus, WithdrawalStatus


class OrderNavigation(BaseModel):
    """Reauthorize this order when opening it."""

    kind: Literal["ORDER"] = "ORDER"
    order_id: UUID


class OccasionNavigation(BaseModel):
    """Reauthorize this saved date when opening it."""

    kind: Literal["OCCASION"] = "OCCASION"
    occasion_id: UUID


class WalletNavigation(BaseModel):
    """Open the authenticated user's wallet."""

    kind: Literal["WALLET"] = "WALLET"


class OrderParameters(BaseModel):
    """The actual order status."""

    model_config = ConfigDict(extra="forbid")

    status: OrderStatus


class WithdrawalParameters(BaseModel):
    """The actual withdrawal status."""

    model_config = ConfigDict(extra="forbid")

    status: WithdrawalStatus


class EmptyParameters(BaseModel):
    """Events that require no interpolation values."""

    model_config = ConfigDict(extra="forbid")

    pass


class NotificationResponse(BaseModel):
    """A safe mobile event with typed destination and localization keys."""

    id: UUID
    type: Literal[
        "ORDER_STATUS_CHANGED",
        "INVOICE_ISSUED",
        "WALLET_TOPUP_COMPLETED",
        "WITHDRAWAL_STATUS_CHANGED",
        "OCCASION_REMINDER",
    ]
    title_key: Literal[
        "notification.order_status_changed.title",
        "notification.invoice_issued.title",
        "notification.wallet_topup_completed.title",
        "notification.withdrawal_status_changed.title",
        "notification.occasion_reminder.title",
    ]
    body_key: Literal[
        "notification.order_status_changed.body",
        "notification.invoice_issued.body",
        "notification.wallet_topup_completed.body",
        "notification.withdrawal_status_changed.body",
        "notification.occasion_reminder.body",
    ]
    parameters: OrderParameters | WithdrawalParameters | EmptyParameters
    created_at: datetime
    read_at: datetime | None
    navigation: Annotated[
        OrderNavigation | OccasionNavigation | WalletNavigation, Field(discriminator="kind")
    ]


class NotificationPage(BaseModel):
    """Newest-first bounded inbox with a database unread count."""

    items: list[NotificationResponse]
    next_cursor: str | None = None
    unread_count: int = Field(ge=0)


class UnreadCount(BaseModel):
    """Current owned unread count."""

    unread_count: int = Field(ge=0)


class ReadAllResponse(BaseModel):
    """Number of unread events marked at the statement cutoff."""

    marked_count: int = Field(ge=0)
    read_at: datetime
