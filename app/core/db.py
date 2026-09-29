"""Async database engine and session factory (SPEC SECTION 3, 16.6).

Uses SQLAlchemy 2.x async with asyncpg. Driver-side prepared-statement caching is
disabled because PgBouncer runs in transaction mode, where a pooled connection may
serve different backends across statements and a cached plan can bind to the wrong
one. Sessions never rely on session-level state across requests.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.middleware import current_request_id

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
                    "entity_type": row.entity_type,
                    "entity_id": str(row.entity_id) if row.entity_id else None,
                },
            },
        )


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine for the configured database URL."""
    return create_async_engine(
        settings.DATABASE_URL.get_secret_value(),
        echo=False if settings.is_production else settings.DEBUG,
        hide_parameters=True,
        pool_pre_ping=True,
        # PgBouncer transaction mode: no server-side statement cache.
        connect_args={"statement_cache_size": 0},
    )


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
