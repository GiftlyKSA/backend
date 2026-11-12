"""Async database engine and session factory.

Uses SQLAlchemy 2.x async with asyncpg. The asyncpg driver cache is disabled.
SQLAlchemy's prepared-statement cache is configured separately by its dialect.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection, ExceptionContext
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.exceptions import DatabaseTimeoutError
from app.core.middleware import current_request_id
from app.core.query_metrics import after_query, before_query

_audit_logger = logging.getLogger("giftly.audit")


def emit_committed_audit_events(session: AsyncSession) -> None:
    """Mirror committed audit metadata to the application log stream."""
    for row in session.info.pop("committed_audit_events", []):
        if not inspect(row).persistent:
            continue
        _audit_logger.info(
            "audit_event_committed",
            extra={
                "request_id": current_request_id(),
                "extra_fields": {
                    "action": row.action,
                    "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                    "actor_category": (row.audit_metadata or {}).get("actor_category"),
                    "entity_type": row.entity_type,
                    "entity_id": str(row.entity_id) if row.entity_id else None,
                },
            },
        )


def _translate_command_timeout(context: ExceptionContext) -> DatabaseTimeoutError | None:
    original = context.original_exception
    if isinstance(original, TimeoutError) or getattr(original, "sqlstate", None) in {
        "55P03",  # lock timeout
        "57014",  # statement canceled
    }:
        return DatabaseTimeoutError()
    return None


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine for the configured database URL."""
    engine = create_async_engine(
        settings.DATABASE_URL.get_secret_value(),
        echo=False if settings.is_production else settings.DEBUG,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_SECONDS,
        connect_args={
            "statement_cache_size": 0,
            "command_timeout": settings.DB_COMMAND_TIMEOUT_SECONDS,
        },
    )
    event.listen(engine.sync_engine, "before_cursor_execute", before_query)
    event.listen(engine.sync_engine, "after_cursor_execute", after_query)
    event.listen(engine.sync_engine, "handle_error", _translate_command_timeout, retval=True)
    if settings.DB_LOCK_TIMEOUT_MS:
        lock_timeout = int(settings.DB_LOCK_TIMEOUT_MS)

        def set_lock_timeout(connection: Connection) -> None:
            connection.exec_driver_sql(f"SET LOCAL lock_timeout = {lock_timeout}")

        event.listen(engine.sync_engine, "begin", set_lock_timeout)
    return engine


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
            emit_committed_audit_events(session)
        except Exception:
            await session.rollback()
            session.info.pop("committed_audit_events", None)
            raise
