"""Structured JSON logging with a mandatory scrubbing filter.

A log file full of OTPs turns a log-read into a breach, so scrubbing is not optional:
the filter redacts by key name AND by regex before any record is emitted. All logs
carry a ``request_id`` when one is bound by the request middleware.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Key names whose values must never be logged (case-insensitive substring match).
_SENSITIVE_KEYS = (
    "otp",
    "password",
    "secret",
    "token",
    "authorization",
    "jwt",
    "refresh",
    "national_id",
    "passport",
    "iban",
    "encryption",
    "api_key",
    "webhook_secret",
    "content_encrypted",
    "session_token",
    "pepper",
    "email",
    "payload",
    "headers",
    "cookies",
    "body",
)

# Value patterns to redact even when the key is innocuous.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+")
_PHONE_RE = re.compile(r"\+9665\d{8}")
_LONG_DIGITS_RE = re.compile(r"\b\d{9,}\b")

_URL_RE = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?(?:\+asyncpg)?|rediss?)://[^\s<>]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_\x60{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|secret|token|otp|api_key|authorization|signature)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_REDACTED = "***REDACTED***"


def _mask_phone(match: re.Match[str]) -> str:
    """Mask a Saudi mobile as ``+9665•••••67``."""
    phone = match.group(0)
    return f"{phone[:5]}•••••{phone[-2:]}"


def scrub_value(value: Any) -> Any:
    """Recursively redact sensitive keys and value patterns in a log payload."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(k) else scrub_value(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_value(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_KEYS)


def scrub_text(text: str) -> str:
    """Redact JWTs, bearer tokens, long id-like digit runs; mask phone numbers."""
    text = _URL_RE.sub(_REDACTED, text)
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _JWT_RE.sub(_REDACTED, text)
    text = _BEARER_RE.sub(_REDACTED, text)
    text = _SECRET_ASSIGNMENT_RE.sub(_REDACTED, text)
    text = _PHONE_RE.sub(_mask_phone, text)
    text = _LONG_DIGITS_RE.sub(_REDACTED, text)
    return text


def _safe_message(record: logging.LogRecord) -> str:
    safe_record = logging.makeLogRecord(record.__dict__.copy())
    if isinstance(record.args, tuple):
        safe_record.args = tuple(
            type(value).__name__ if isinstance(value, BaseException) else scrub_value(value)
            for value in record.args
        )
    elif isinstance(record.args, dict):
        safe_record.args = scrub_value(record.args)
    return scrub_text(safe_record.getMessage())


class ScrubbingJsonFormatter(logging.Formatter):
    """Emits one scrubbed JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a scrubbed JSON log line."""
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": _safe_message(record),
        }
        request_id = getattr(record, "request_id", None)
        if request_id is not None:
            payload["request_id"] = request_id
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload["fields"] = scrub_value(extra)
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            frames = traceback.extract_tb(record.exc_info[2]) if record.exc_info[2] else []
            if frames:
                last = frames[-1]
                payload["exception_location"] = scrub_text(
                    f"{Path(last.filename).name}:{last.lineno}:{last.name}"
                )
        return json.dumps(payload, default=str)


def _safe_line(value: object, limit: int = 500) -> str:
    text = scrub_text(str(value))
    return "".join(character if character.isprintable() else " " for character in text)[:limit]


class ScrubbingFormatter(ScrubbingJsonFormatter):
    """Keep operational JSON and emit bounded, private error blocks."""

    def format(self, record: logging.LogRecord) -> str:
        """Include safe context without exception messages, locals or request bodies."""
        if record.levelno < logging.ERROR and not record.exc_info:
            return super().format(record)
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds")
        lines = [
            "-" * 47 + "ERROR" + "-" * 40,
            f"{timestamp} | {_safe_line(record.levelname)} | {_safe_line(record.name, 150)}",
            f"Message: {_safe_line(_safe_message(record), 1000)}",
        ]
        request_id = getattr(record, "request_id", None)
        if request_id is not None:
            lines.append(f"Request ID: {_safe_line(request_id, 64)}")
        if record.exc_info and record.exc_info[0] is not None:
            lines.append(f"Exception: {_safe_line(record.exc_info[0].__name__, 100)}")
            frames = traceback.extract_tb(record.exc_info[2], limit=-1)
            if frames:
                last = frames[-1]
                location = f"{Path(last.filename).name}:{last.lineno}:{last.name}"
                lines.append(f"Location: {_safe_line(location)}")
        else:
            location = f"{Path(record.pathname).name}:{record.lineno}:{record.funcName}"
            lines.append(f"Location: {_safe_line(location)}")
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            allowed = {
                "code",
                "status_code",
                "method",
                "route",
                "operation",
                "task_id",
                "order_id",
                "invoice_id",
                "exception_type",
                "exception_location",
                "attempt",
            }
            context = {
                key: _safe_line(value, 150)
                for key, value in extra.items()
                if key in allowed and isinstance(value, (str, int, float, bool))
            }
            if context:
                lines.append("Context: " + _safe_line(json.dumps(context), 800))
        return "\n".join([*lines[:7], "-" * 95])


def configure_logging(level: str = "INFO") -> None:
    """Install the scrubbing JSON formatter as the root handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(ScrubbingFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "taskiq", "taskiq.worker"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
