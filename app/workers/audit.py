"""Persist metadata-only audit outcomes for scheduled system jobs."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory, emit_committed_audit_events
from app.repositories.audit_repository import AuditRepository

_logger = logging.getLogger("app.workers.audit")
Job = Callable[[], Awaitable[None]]


async def _record_job_outcome(name: str, outcome: str) -> None:
    engine = build_engine(get_settings())
    try:
        factory = build_session_factory(engine)
        async with factory() as session:
            session.info["audit_actor_category"] = "SYSTEM"
            await AuditRepository(session).record(
                actor_user_id=None,
                action="SYSTEM_JOB_RUN",
                entity_type="scheduled_job",
                entity_id=None,
                metadata={"job": name, "outcome": outcome},
            )
            await session.commit()
            emit_committed_audit_events(session)
    finally:
        await engine.dispose()


def audited_system_job(name: str) -> Callable[[Job], Job]:
    """Record the outcome of a scheduled job without hiding its original failure."""

    def decorate(job: Job) -> Job:
        @wraps(job)
        async def wrapper() -> None:
            try:
                await _record_job_outcome(name, "STARTED")
            except Exception:
                _logger.exception("Failed to save the start audit event for system job %s", name)
            outcome = "COMPLETED"
            try:
                await job()
            except Exception:
                outcome = "FAILED"
                raise
            finally:
                try:
                    await _record_job_outcome(name, outcome)
                except Exception:
                    _logger.exception("Failed to save the audit event for system job %s", name)

        return wrapper

    return decorate
