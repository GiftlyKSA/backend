"""Strict Gregorian calendar ranges shared by schedule list endpoints."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import Query
from fastapi.exceptions import RequestValidationError
from pydantic import BeforeValidator


def _calendar_date(value: object) -> object:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError("Use a Gregorian date in YYYY-MM-DD format.")
    return value


CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]


@dataclass(frozen=True)
class DateRange:
    """Inclusive date-only bounds, without timezone conversions."""

    from_date: date | None
    to_date: date | None


def date_range(
    from_date: Annotated[
        CalendarDate | None, Query(description="Inclusive start date (Gregorian YYYY-MM-DD).")
    ] = None,
    to_date: Annotated[
        CalendarDate | None, Query(description="Inclusive end date (Gregorian YYYY-MM-DD).")
    ] = None,
) -> DateRange:
    """Validate paired query boundaries using the existing HTTP 422 response."""
    if from_date is not None and to_date is not None and from_date > to_date:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "to_date"),
                    "msg": "to_date must be on or after from_date.",
                    "input": to_date.isoformat(),
                }
            ]
        )
    return DateRange(from_date, to_date)
