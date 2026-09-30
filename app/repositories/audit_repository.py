"""Audit-log persistence (SPEC SECTION 8.19).

Every security-relevant and admin action writes one append-only row here. Metadata is
scrubbed of Restricted data before it reaches this layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import literal_column, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


class AuditRepository:
    """Reads and appends audit-log rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def record(
        self,
        *,
        actor_user_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        ip_address: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AuditLog:
        """Append one audit row and flush it."""
        actor_category = self._session.info.get("audit_actor_category")
        if actor_category not in {"ADMIN", "USER", "SYSTEM", "ANONYMOUS"}:
            actor_category = "USER" if actor_user_id else "SYSTEM"
        row = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=ip_address,
            audit_metadata={**(metadata or {}), "actor_category": actor_category},
        )
        self._session.add(row)
        await self._session.flush()
        self._session.info.setdefault("committed_audit_events", []).append(row)
        return row

    async def list_recent(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        actor_category: str | None = None,
        before_at: datetime | None = None,
        before_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Return recent audit rows, newest first, with optional filters."""
        query = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(max(1, min(limit, 101)))
        )
        if actor_user_id is not None:
            query = query.where(AuditLog.actor_user_id == actor_user_id)
        if action is not None:
            query = query.where(AuditLog.action == action)
        if entity_type is not None:
            query = query.where(AuditLog.entity_type == entity_type)
        if actor_category is not None:
            query = query.where(
                AuditLog.audit_metadata.op("->>")(literal_column("'actor_category'"))
                == actor_category
            )
        if before_at is not None and before_id is not None:
            query = query.where(tuple_(AuditLog.created_at, AuditLog.id) < (before_at, before_id))
        return list(await self._session.scalars(query))
