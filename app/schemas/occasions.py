"""Customer-owned special-date contracts."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CreateOccasionRequest(BaseModel):
    """Store a special date with optional one-off reminder delivery."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    occasion_date: date
    recurrence: Literal["NONE", "ANNUAL"] = "NONE"
    reminder_enabled: bool = Field(default=False, strict=True)
    reminder_days_before: int = Field(default=7, ge=0, le=365, strict=True)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        """Reject empty labels while preserving user text."""
        if not value.strip():
            raise ValueError("Title must not be blank.")
        return value


class UpdateOccasionRequest(BaseModel):
    """Change supplied fields only; null is not a valid field value."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    occasion_date: date | None = None
    recurrence: Literal["NONE", "ANNUAL"] | None = None
    reminder_enabled: bool | None = Field(default=None, strict=True)
    reminder_days_before: int | None = Field(default=None, ge=0, le=365, strict=True)

    @field_validator(
        "title", "occasion_date", "reminder_days_before", "reminder_enabled", "recurrence"
    )
    @classmethod
    def non_null(cls, value: object) -> object:
        """Distinguish omitted properties from explicit nulls."""
        if value is None:
            raise ValueError("Omit unchanged fields instead of sending null.")
        if isinstance(value, str) and not value.strip():
            raise ValueError("Title must not be blank.")
        return value

    @model_validator(mode="after")
    def non_empty(self) -> "UpdateOccasionRequest":
        """Require at least one actual change request."""
        if not self.model_fields_set:
            raise ValueError("Supply at least one field to update.")
        return self


class OccasionResponse(BaseModel):
    """A saved calendar date with UTC record timestamps."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    occasion_date: date
    reminder_days_before: int
    recurrence: Literal["NONE", "ANNUAL"] = "NONE"
    reminder_enabled: bool = False
    timezone: Literal["Asia/Riyadh"] = "Asia/Riyadh"
    created_at: datetime
    updated_at: datetime


class OccasionPage(BaseModel):
    """A bounded page ordered by calendar date and ID."""

    items: list[OccasionResponse]
    next_cursor: UUID | None = None


class OccasionOccurrence(BaseModel):
    """One virtual date, distinct from its saved occasion identity."""

    occasion_id: UUID
    title: str
    occurrence_date: date
    recurrence: Literal["NONE", "ANNUAL"]
    reminder_enabled: bool
    reminder_days_before: int
    timezone: Literal["Asia/Riyadh"] = "Asia/Riyadh"


class OccasionOccurrencePage(BaseModel):
    """Bounded expanded dates ordered by occurrence date and occasion ID."""

    items: list[OccasionOccurrence]
    next_cursor: str | None = None
