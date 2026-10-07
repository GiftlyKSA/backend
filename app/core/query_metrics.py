"""Count SQL work per request without retaining query text or parameters."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.engine import Connection, ExecutionContext


@dataclass
class QueryMetrics:
    """Aggregate elapsed database execution time, including network round trips."""

    count: int = 0
    elapsed_seconds: float = 0


_metrics: ContextVar[QueryMetrics | None] = ContextVar("query_metrics", default=None)


def begin_query_metrics() -> tuple[QueryMetrics, Token[QueryMetrics | None]]:
    """Start an isolated request measurement shared by its async tasks."""
    metrics = QueryMetrics()
    return metrics, _metrics.set(metrics)


def end_query_metrics(token: Token[QueryMetrics | None]) -> None:
    """Restore the enclosing request context."""
    _metrics.reset(token)


def before_query(
    connection: Connection,
    cursor: object,
    statement: str,
    parameters: object,
    context: ExecutionContext,
    executemany: bool,
) -> None:
    """Start a successful SQL statement timer only during measured requests."""
    if _metrics.get() is not None:
        connection.info["giftly_query_started"] = perf_counter()


def after_query(
    connection: Connection,
    cursor: object,
    statement: str,
    parameters: object,
    context: ExecutionContext,
    executemany: bool,
) -> None:
    """Accumulate elapsed execution time without recording SQL or values."""
    started = connection.info.pop("giftly_query_started", None)
    metrics = _metrics.get()
    if metrics is not None and isinstance(started, float):
        metrics.count += 1
        metrics.elapsed_seconds += perf_counter() - started
