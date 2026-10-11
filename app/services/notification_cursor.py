"""Opaque UUID anchors validated within their authorized resource scope."""

import base64
import binascii
from uuid import UUID

from app.core.exceptions import ValidationDomainError


def encode_cursor(identifier: UUID) -> str:
    """Encode a stable record anchor."""
    return base64.urlsafe_b64encode(identifier.bytes).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> UUID:
    """Reject oversized or noncanonical cursors before database access."""
    if len(value) != 22:
        raise ValidationDomainError("Invalid cursor.")
    try:
        raw = base64.b64decode(value + "==", altchars=b"-_", validate=True)
        identifier = UUID(bytes=raw)
    except (ValueError, binascii.Error) as exc:
        raise ValidationDomainError("Invalid cursor.") from exc
    if encode_cursor(identifier) != value:
        raise ValidationDomainError("Invalid cursor.")
    return identifier
