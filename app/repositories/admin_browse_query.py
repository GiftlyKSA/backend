"""Validated, bounded queries for dashboard table lists."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Uuid,
    select,
    tuple_,
)
from sqlalchemy.sql import Select
from sqlalchemy.sql.schema import Column, Table

from app.core.admin_time import ADMIN_TIMEZONE
from app.core.exceptions import ValidationDomainError


@dataclass(frozen=True)
class BrowseOptions:
    """One page's filters, sort direction, and stable cursor."""

    page_size: int = 25
    sort_by: str = ""
    direction: Literal["asc", "desc"] = "desc"
    filter_field: str = ""
    filter_value: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    after: uuid.UUID | None = None
    before: uuid.UUID | None = None
    cursor_at: datetime | date | None = None
    scope_field: str = ""
    scope_value: str = ""


def date_columns(table: Table) -> list[str]:
    """Expose non-null date columns for stable chronological pagination."""
    return [
        c.name for c in table.columns if isinstance(c.type, (DateTime, Date)) and not c.nullable
    ]


def filter_value(column: Column[Any], value: str) -> object:
    """Convert exact-match input to the column's scalar type."""
    try:
        if isinstance(column.type, Boolean):
            return _boolean_value(value)
        if isinstance(column.type, Uuid):
            return uuid.UUID(value)
        if isinstance(column.type, DateTime):
            parsed = datetime.fromisoformat(value)
            return parsed.replace(tzinfo=ADMIN_TIMEZONE) if parsed.tzinfo is None else parsed
        if isinstance(column.type, Date):
            return date.fromisoformat(value)
        if isinstance(column.type, Enum):
            if value not in column.type.enums:
                raise ValueError("Unsupported choice")
            return value
        if isinstance(column.type, Numeric):
            return _finite_decimal(value)
        if column.type.python_type is int:
            return _integer_value(column, value)
        return column.type.python_type(value)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValidationDomainError(f"Invalid filter value for {column.name}.") from exc


def _boolean_value(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError("Expected true or false")
    return value.lower() == "true"


def _finite_decimal(value: str) -> Decimal:
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("Expected finite number")
    return number


def _integer_value(column: Column[Any], value: str) -> int:
    number = int(value)
    bits = (
        64
        if isinstance(column.type, BigInteger)
        else 16
        if isinstance(column.type, SmallInteger)
        else 32
    )
    if not -(2 ** (bits - 1)) <= number < 2 ** (bits - 1):
        raise ValueError("Integer outside column range")
    return number


def browse_query(table: Table, options: BrowseOptions, fields: list[str]) -> Select[Any]:
    """Build allowlisted filters and keyset ordering without OFFSET or raw SQL."""
    dates = date_columns(table)
    key = next(iter(table.primary_key.columns))
    sort_name = options.sort_by or ("created_at" if "created_at" in dates else key.name)
    if sort_name not in [*dates, key.name]:
        raise ValidationDomainError("Choose an available date field or record ID to sort.")
    if options.page_size not in {25, 50, 100}:
        raise ValidationDomainError("Page size must be 25, 50, or 100.")
    if options.after and options.before:
        raise ValidationDomainError("Choose one page cursor.")
    sort_column = table.c[sort_name]
    query = select(table)
    query = _attribute_filters(query, table, options, fields)
    query = _date_bounds(query, sort_column, options)
    cursor_id = options.after or options.before
    ascending = (options.direction == "asc") != (options.before is not None)
    if cursor_id is not None:
        query = _cursor_query(query, sort_column, key, options, cursor_id, ascending)
    ordering = [sort_column.asc() if ascending else sort_column.desc()]
    if sort_name != key.name:
        ordering.append(key.asc() if ascending else key.desc())
    return query.order_by(*ordering).limit(options.page_size + 1)


def _attribute_filters(
    query: Select[Any], table: Table, options: BrowseOptions, fields: list[str]
) -> Select[Any]:
    for name, value in (
        (options.filter_field, options.filter_value),
        (options.scope_field, options.scope_value),
    ):
        if name:
            if name not in fields:
                raise ValidationDomainError("Choose an available filter field.")
            if value:
                column = table.c[name]
                query = query.where(column == filter_value(column, value))
    return query


def _cursor_query(
    query: Select[Any],
    sort_column: Column[Any],
    key: Column[Any],
    options: BrowseOptions,
    cursor_id: uuid.UUID,
    ascending: bool,
) -> Select[Any]:
    if sort_column.name == key.name:
        return query.where(key > cursor_id if ascending else key < cursor_id)
    if options.cursor_at is None:
        raise ValidationDomainError("Date pagination requires a date cursor.")
    cursor_at = options.cursor_at
    if isinstance(sort_column.type, Date) and isinstance(cursor_at, datetime):
        cursor_at = cursor_at.date()
    position = tuple_(sort_column, key)
    anchor = (cursor_at, cursor_id)
    return query.where(position > anchor if ascending else position < anchor)


def _date_bounds(query: Select[Any], column: Column[Any], options: BrowseOptions) -> Select[Any]:
    if options.start_at and options.end_at and options.start_at > options.end_at:
        raise ValidationDomainError("Start time must not exceed end time.")
    if (options.start_at or options.end_at) and not isinstance(column.type, (DateTime, Date)):
        raise ValidationDomainError("Choose a date field before filtering by date.")
    for bound, lower in ((options.start_at, True), (options.end_at, False)):
        if bound is not None:
            value: datetime | date = bound.date() if isinstance(column.type, Date) else bound
            query = query.where(column >= value if lower else column <= value)
    return query


def scalar_column(column: Column[Any]) -> bool:
    """Keep JSON, binary, and unsupported values out of filter menus."""
    return isinstance(column.type, (String, Uuid, Boolean, Numeric, Integer, DateTime, Date))
