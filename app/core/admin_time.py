"""Riyadh display helpers; database timestamps retain their UTC instants."""

from datetime import UTC, datetime, timedelta, timezone

ADMIN_TIMEZONE = timezone(timedelta(hours=3), "UTC+3")


def database_datetime(value: datetime) -> datetime:
    """Normalize a timezone-aware instant before binding a database timestamp."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(UTC)


def admin_datetime(value: datetime | str) -> str:
    """Format an instant in the dashboard's fixed Riyadh timezone."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    aware = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return aware.astimezone(ADMIN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S UTC+3")


def admin_input(value: datetime | None) -> datetime | None:
    """Interpret offset-free datetime picker input as Riyadh time."""
    return value.replace(tzinfo=ADMIN_TIMEZONE) if value and value.tzinfo is None else value


def finalize_admin_value(value: object) -> object:
    """Localize rendered datetime objects while preserving date-only values."""
    return admin_datetime(value) if isinstance(value, datetime) else value
