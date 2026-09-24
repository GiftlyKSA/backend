"""Typed form descriptions and validation for authenticated admin table maintenance."""

from __future__ import annotations

import ipaddress
import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Table, Text
from sqlalchemy.dialects.postgresql import ENUM, INET, JSONB, UUID

from app.core.exceptions import ValidationDomainError

_GENERATED = {"created_at", "updated_at", "auth_version"}
_SECRET_MARKERS = ("encrypted", "token", "secret", "hash", "fingerprint", "password")
MAX_FIELD_LENGTH = 16_384


@dataclass(frozen=True)
class TableField:
    """One scalar or relationship input; secrets are never pre-populated."""

    name: str
    label: str
    kind: str
    value: str
    required: bool
    nullable: bool
    secret: bool
    choices: tuple[str, ...] = ()
    maxlength: int = MAX_FIELD_LENGTH
    readonly: bool = False
    selected_label: str = ""


def is_secret(name: str) -> bool:
    """Identify stored credentials and encrypted content that must remain write-only."""
    return any(marker in name for marker in _SECRET_MARKERS)


def editable_columns(table: Table, *, creating: bool) -> list[Column[Any]]:
    """Keep generated identifiers and timestamps under database control."""
    return [
        column
        for column in table.c
        if column.name not in _GENERATED
        and (not column.primary_key or (creating and column.foreign_keys))
    ]


def _kind(column: Column[Any]) -> str:
    if column.foreign_keys:
        return "relationship"
    for sql_type, kind in (
        (ENUM, "enum"),
        (Boolean, "boolean"),
        (DateTime, "datetime"),
        (Date, "date"),
        (JSONB, "json"),
        (Geometry, "point"),
        (Text, "textarea"),
        (Numeric, "number"),
        (Integer, "number"),
    ):
        if isinstance(column.type, sql_type):
            return kind
    return "text"


def _format(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def form_fields(table: Table, row: dict[str, Any] | None) -> list[TableField]:
    """Describe all form fields with defaults, nullability, and secret handling."""
    fields = []
    for column in editable_columns(table, creating=True):
        secret = is_secret(column.name)
        value = "" if secret or row is None else _format(row.get(column.name))
        if row is not None and isinstance(column.type, JSONB) and row.get(column.name) is not None:
            value = json.dumps(row[column.name], ensure_ascii=False)
        fields.append(
            TableField(
                name=column.name,
                label=column.name.removesuffix("_encrypted").replace("_", " ").title(),
                kind=_kind(column),
                value=value,
                required=(
                    row is None
                    and not column.nullable
                    and column.server_default is None
                    and column.name != "identity_fingerprint"
                ),
                nullable=bool(column.nullable),
                secret=secret,
                choices=tuple(column.type.enums) if isinstance(column.type, ENUM) else (),
                maxlength=min(
                    getattr(column.type, "length", None) or MAX_FIELD_LENGTH, MAX_FIELD_LENGTH
                ),
                readonly=row is not None and column.primary_key,
            )
        )
    return fields


def _numeric(column: Column[Any], raw: str) -> Decimal:
    value = Decimal(raw)
    assert isinstance(column.type, Numeric)
    scale = column.type.scale or 0
    precision = column.type.precision or 38
    if not value.is_finite() or value.copy_abs() >= Decimal(10) ** (precision - scale):
        raise ValueError("Invalid decimal")
    if value != value.quantize(Decimal(1).scaleb(-scale)):
        raise ValueError("Too many decimal places")
    return value


def _point(raw: str) -> str:
    longitude, latitude = (Decimal(part.strip()) for part in raw.split(","))
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError("Coordinates out of range")
    return f"SRID=4326;POINT({longitude} {latitude})"


def _structured(column: Column[Any], raw: str) -> object:
    if isinstance(column.type, JSONB):
        return json.loads(
            raw, parse_constant=lambda value: _invalid_json(), parse_float=_json_float
        )
    if isinstance(column.type, Geometry):
        return _point(raw)
    if isinstance(column.type, INET):
        return str(ipaddress.ip_address(raw))
    if isinstance(column.type, UUID):
        return uuid.UUID(raw)
    if isinstance(column.type, DateTime):
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return parsed
    if isinstance(column.type, Date):
        return date.fromisoformat(raw)
    return raw


def _invalid_json() -> object:
    raise ValueError("Non-finite JSON number")


def _json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    return value


def _parse(column: Column[Any], raw: str) -> object:
    if isinstance(column.type, ENUM):
        if raw not in column.type.enums:
            raise ValueError("Invalid enum")
        return raw
    if isinstance(column.type, Boolean):
        if raw not in ("true", "false"):
            raise ValueError("Invalid boolean")
        return raw == "true"
    if isinstance(column.type, Numeric):
        return _numeric(column, raw)
    if isinstance(column.type, Integer):
        return int(raw)
    if isinstance(column.type, String) and column.type.length and len(raw) > column.type.length:
        raise ValueError("Text is too long")
    return _structured(column, raw)


def _field_value(
    column: Column[Any], values: dict[str, str], creating: bool
) -> tuple[bool, object]:
    name = column.name
    raw = values.get(name, "")
    if values.get(f"null__{name}") == "1":
        if not column.nullable:
            raise ValueError("Required field cannot be null")
        return True, None
    if raw == "":
        if (
            creating
            and not column.nullable
            and column.server_default is None
            and name != "identity_fingerprint"
        ):
            raise ValueError("Required field")
        return False, None
    if len(raw) > MAX_FIELD_LENGTH:
        raise ValueError("Field is too large")
    return True, _parse(column, raw)


def parse_values(table: Table, values: dict[str, str], *, creating: bool) -> dict[str, Any]:
    """Validate an explicit field allowlist and parse values without lossy money conversions."""
    columns = editable_columns(table, creating=creating)
    allowed = {column.name for column in columns}
    allowed.update(f"null__{column.name}" for column in columns if column.nullable)
    if values.keys() - allowed:
        raise ValidationDomainError("Unknown or read-only fields were submitted.")
    parsed: dict[str, Any] = {}
    for column in columns:
        try:
            include, value = _field_value(column, values, creating)
        except (ValueError, InvalidOperation, OverflowError, RecursionError) as exc:
            raise ValidationDomainError(f"Check the value for {column.name}.") from exc
        if include:
            parsed[column.name] = value
    return parsed
