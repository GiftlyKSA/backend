"""Convert financial reporting days to indexed UTC timestamp boundaries."""

from datetime import UTC, date, datetime, time, timedelta, timezone

from app.core.exceptions import ValidationDomainError

_RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")


def reporting_bounds(
    from_date: date | None, to_date: date | None
) -> tuple[datetime | None, datetime | None]:
    """Return an inclusive start and exclusive end for Riyadh calendar dates."""
    if to_date == date.max or from_date == date.min:
        raise ValidationDomainError("Reporting dates cannot use the calendar extremes.")
    start = (
        datetime.combine(from_date, time.min, _RIYADH).astimezone(UTC)
        if from_date is not None
        else None
    )
    end = (
        datetime.combine(to_date + timedelta(days=1), time.min, _RIYADH).astimezone(UTC)
        if to_date is not None
        else None
    )
    return start, end
