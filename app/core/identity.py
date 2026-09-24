"""Canonical courier identity selection shared by registration and maintenance."""

from app.core.exceptions import ValidationDomainError
from app.core.security import hmac_hex


def identity_fingerprint(national_id: str | None, passport_id: str | None, pepper: str) -> str:
    """Prefer a nonblank national ID, falling back to a nonblank passport."""
    canonical = (national_id or "").strip() or (passport_id or "").strip()
    if not canonical:
        raise ValidationDomainError("A courier must provide a national id or passport.")
    return hmac_hex(canonical, pepper)
