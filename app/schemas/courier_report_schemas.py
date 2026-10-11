"""Courier report and target contracts."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.money import money_str, parse_money

Granularity = Literal["day", "week", "month"]
TargetPeriod = Literal["WEEK", "MONTH"]
Comparison = Literal["INCREASE", "DECREASE", "UNCHANGED", "NO_BASELINE"]


class TargetUpdate(BaseModel):
    """Update supplied target preferences using the last observed version."""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0, le=2147483646, strict=True)
    target_enabled: bool | None = Field(default=None, strict=True)
    target_amount: str | None = None
    target_period: TargetPeriod | None = None

    @field_validator("target_amount", mode="before")
    @classmethod
    def validate_amount(cls, value: object) -> str | None:
        """Require finite, bounded decimal string money."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Use a decimal string for amount.")
        if len(value) > 32:
            raise ValueError("Amount string is too long.")
        try:
            raw = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Use a finite decimal amount.") from exc
        if not raw.is_finite() or raw <= 0 or raw > 1000000:
            raise ValueError("Amount must be positive and at most 1000000.00.")
        amount = parse_money(value)
        if amount <= 0 or amount > 1000000:
            raise ValueError("Amount must be positive and at most 1000000.00.")
        return money_str(amount)

    @model_validator(mode="after")
    def validate_change(self) -> "TargetUpdate":
        """Require a preference change and reject explicit null enabled state."""
        if self.model_fields_set == {"version"}:
            raise ValueError("Supply at least one target preference.")
        if "target_enabled" in self.model_fields_set and self.target_enabled is None:
            raise ValueError("target_enabled cannot be null.")
        return self


class TargetProgress(BaseModel):
    """Actual settled proceeds in the complete current target period."""

    enabled: Literal[True] = True
    period: TargetPeriod
    amount: str
    period_start: date
    period_end: date
    settled_total: str
    progress_percent: str


class TargetSettings(BaseModel):
    """Versioned target settings; financial notifications are not offered."""

    version: int
    updated_at: datetime | None
    target_enabled: bool
    target_amount: str | None
    target_period: TargetPeriod | None
    progress: TargetProgress | None


class EarningsBucket(BaseModel):
    """A clipped calendar bucket within the requested inclusive range."""

    from_date: date
    to_date: date
    amount: str


class PreviousPeriod(BaseModel):
    """Equal-length immediately preceding proceeds comparison."""

    from_date: date
    to_date: date
    settled_total: str
    difference: str
    percentage: str | None
    comparison: Comparison


class EarningsReport(BaseModel):
    """Historical signed settled courier proceeds including item reimbursement."""

    currency: Literal["SAR"] = "SAR"
    timezone: Literal["Asia/Riyadh"] = "Asia/Riyadh"
    from_date: date
    to_date: date
    granularity: Granularity
    basis: Literal["SETTLED_COURIER_PROCEEDS"] = "SETTLED_COURIER_PROCEEDS"
    settled_total: str
    pending_total: None = None
    previous: PreviousPeriod
    buckets: list[EarningsBucket]
    target: TargetProgress | None
