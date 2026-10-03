"""Canonical promo-code spelling shared by API and persistence boundaries."""


def normalize_code(code: str) -> str:
    """Accept every letter case and trim surrounding whitespace."""
    return code.strip().upper()
