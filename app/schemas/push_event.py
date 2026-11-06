"""Allowlisted private push hints, independent of inbox read state."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class PushEvent(BaseModel):
    """Only resource references needed to refresh an authorized screen."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: Literal["ORDER_AVAILABLE", "CHAT_MESSAGE", "OCCASION_REMINDER"]
    type: Literal["ORDER_AVAILABLE", "OCCASION_REMINDER"] | None = None
    kind: Literal["OCCASION"] | None = None
    order_id: UUID | None = None
    conversation_id: UUID | None = None
    occasion_id: UUID | None = None
    reminder_version: int | None = Field(default=None, ge=1)

    @field_serializer("reminder_version", when_used="json")
    def serialize_version(self, value: int | None) -> str | None:
        """Push data preserves its string-only transport representation."""
        return str(value) if value is not None else None

    @model_validator(mode="after")
    def validate_destination(self) -> Self:
        """Reject mixed or incomplete destinations before persisting hints."""
        if self.event_type == "ORDER_AVAILABLE":
            valid = (
                self.order_id is not None
                and self.type == "ORDER_AVAILABLE"
                and self.conversation_id is None
                and self.occasion_id is None
                and self.reminder_version is None
                and self.kind is None
            )
        elif self.event_type == "CHAT_MESSAGE":
            valid = (
                self.conversation_id is not None
                and self.order_id is None
                and self.occasion_id is None
                and self.reminder_version is None
                and self.type is None
                and self.kind is None
            )
        else:
            valid = (
                self.occasion_id is not None
                and self.reminder_version is not None
                and self.type == "OCCASION_REMINDER"
                and self.kind == "OCCASION"
                and self.order_id is None
                and self.conversation_id is None
            )
        if not valid:
            raise ValueError("Invalid push destination.")
        return self

    def wire_data(self) -> dict[str, str]:
        """Retain the existing string-only push data contract."""
        return {key: str(value) for key, value in self.model_dump(exclude_none=True).items()}
