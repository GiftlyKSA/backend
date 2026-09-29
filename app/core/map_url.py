"""Validation for customer-supplied Google Maps links; links are never fetched by the API."""

from urllib.parse import urlsplit


def validate_delivery_map_url(value: str) -> str:
    """Accept only bounded HTTPS Google Maps and Maps short links."""
    if not value or len(value) > 2048 or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("Provide a valid Google Maps link.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Provide a valid Google Maps link.") from exc
    allowed_hosts = {"google.com", "www.google.com", "maps.google.com", "maps.app.goo.gl"}
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise ValueError("Provide a valid HTTPS Google Maps link.")
    if parsed.hostname in {"google.com", "www.google.com"} and not parsed.path.startswith("/maps"):
        raise ValueError("Provide a valid HTTPS Google Maps link.")
    return value
