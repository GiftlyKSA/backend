"""Request correlation and the global exception handler (SPEC SECTION 8.14-17).

Every request gets a ``request_id`` bound into a context var so logs correlate, and
every response echoes it in the ``X-Request-ID`` header. The global handler renders
the §8.16 error envelope and never leaks internals to the client.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import DomainError, RateLimitedError
from app.core.query_metrics import begin_query_metrics, end_query_metrics

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_logger = logging.getLogger("app.request")
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


def current_request_id() -> str:
    """Return the request id bound to the current context, or ``-`` if none."""
    return _request_id_ctx.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns and propagates a per-request correlation id."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the ASGI app."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        """Bind a request id, run the handler, and echo the id back."""
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        token = _request_id_ctx.set(request_id)
        metrics, metrics_token = begin_query_metrics()
        started = perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (perf_counter() - started) * 1000
            if elapsed_ms >= 500:
                route = request.scope.get("route")
                _logger.warning(
                    "Slow request completed; inspect database time and dependency waits.",
                    extra={
                        "request_id": request_id,
                        "extra_fields": {
                            "route": str(getattr(route, "path", "unmatched")),
                            "method": request.method,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "sql_count": metrics.count,
                            "sql_ms": round(metrics.elapsed_seconds * 1000, 2),
                        },
                    },
                )
            end_query_metrics(metrics_token)
            _request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


class BodySizeMiddleware:
    """Bound actual HTTP bytes before parsers, dependencies, or writes run."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        """Wrap an application with a bounded request body buffer."""
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Read a bounded body and replay it, preserving non-HTTP traffic."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                response = error_response(
                    413, "PAYLOAD_TOO_LARGE", "The request body is too large."
                )
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _envelope(code: str, message: str) -> dict[str, object]:
    """Build the §8.16 error envelope with the current request id."""
    return {"error": {"code": code, "message": message, "request_id": current_request_id()}}


def error_response(
    status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    """Render an §8.16 error envelope as a JSON response.

    Shared by the exception handlers and the guard middlewares (rate limit, body size)
    so every error the client sees carries the same shape and the current request id.
    """
    return JSONResponse(
        status_code=status_code, content=_envelope(code, message), headers=headers or {}
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the single global exception handler and a domain-error handler."""

    @app.exception_handler(DomainError)
    async def _handle_domain(request: Request, exc: DomainError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.retry_after_seconds)
        return error_response(exc.status_code, exc.code, exc.message, headers=headers)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # SECURITY: never leak the exception text, SQL, or a stack trace to a client.
        _logger.exception("Unhandled error", extra={"request_id": current_request_id()})
        return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")
