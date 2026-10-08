"""Bounded Dhamen notification schemas; payload amounts are never settlement inputs."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.core.exceptions import ValidationDomainError
from app.integrations.payments.base import PaymentNotification

_Identifier = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class _Header(BaseModel):
    batch_id: _Identifier = Field(alias="BatchId")
    creation_time: datetime = Field(alias="BatchCreationTime")


class _Payment(BaseModel):
    reference: _Identifier = Field(alias="PaymentID")


class _Notification(BaseModel):
    notification_id: int = Field(alias="NotificationId", ge=0, le=9223372036854775807)
    notification_type: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        alias="NotificationType"
    )
    notification_time: datetime = Field(alias="NotificationTime")
    payment: _Payment | list[_Payment] | None = Field(default=None, alias="Payment")


class _Batch(BaseModel):
    header: _Header = Field(alias="Header")
    notifications: list[_Notification] = Field(alias="Notifications", min_length=1, max_length=100)


def parse_dhamen_notifications(raw_body: bytes) -> tuple[PaymentNotification, ...]:
    """Accept documented object/list variants and bound references before status calls."""
    if len(raw_body) > 65536:
        raise ValidationDomainError("Dhamen callback exceeds its size limit.")
    try:
        batch = _Batch.model_validate_json(raw_body)
    except ValidationError as exc:
        raise ValidationDomainError("Malformed Dhamen notification batch.") from exc
    result = []
    for notification in batch.notifications:
        payment = notification.payment
        payments = payment if isinstance(payment, list) else [payment] if payment else []
        should_reconcile = notification.notification_type in {
            "Deposit_Notification",
            "Payment_Failed_Notification",
            "Payment_Settled_Notification",
        }
        if len(payments) > 20 or (should_reconcile and not payments):
            raise ValidationDomainError("Invalid Dhamen notification payment references.")
        result.append(
            PaymentNotification(
                batch_id=batch.header.batch_id,
                notification_id=str(notification.notification_id),
                notification_type=notification.notification_type,
                references=tuple(row.reference for row in payments),
                should_reconcile=should_reconcile,
            )
        )
    if sum(len(item.references) for item in result) > 100:
        raise ValidationDomainError("Too many Dhamen notification references.")
    return tuple(result)
