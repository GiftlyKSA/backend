"""Audit-log persistence (SPEC SECTION 8.19).

Every security-relevant and admin action writes one append-only row here. Metadata is
scrubbed of Restricted data before it reaches this layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import literal_column, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_time import database_datetime
from app.models import AuditLog, User


@dataclass(frozen=True)
class AuditActor:
    """Current actor identity shown only to authenticated dashboard administrators."""

    id: uuid.UUID
    full_name: str | None
    email: str | None


@dataclass(frozen=True)
class AuditFilterChoices:
    """Bounded stored action/entity choices for one activity category."""

    actions: list[str]
    entities: list[str]


class AuditRepository:
    """Reads and appends audit-log rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def list_actors(self, actor_ids: set[uuid.UUID]) -> dict[uuid.UUID, AuditActor]:
        """Fetch the visible page's actor labels in one bounded query."""
        if not actor_ids:
            return {}
        if len(actor_ids) > 100:
            raise ValueError("Audit actor lookup is limited to one page.")
        result = await self._session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(actor_ids))
        )
        return {
            actor_id: AuditActor(actor_id, full_name, email)
            for actor_id, full_name, email in result
        }

    async def filter_choices(self, categories: tuple[str, ...]) -> AuditFilterChoices:
        """Load dropdown values only for the selected tab, without reading row bodies."""
        category = AuditLog.audit_metadata.op("->>")(literal_column("'actor_category'"))
        values: list[list[str]] = []
        for column in (AuditLog.action, AuditLog.entity_type):
            query = (
                select(column)
                .distinct()
                .where(category.in_(categories))
                .where(AuditLog.action.not_like("HTTP\\_%", escape="\\"))
                .where(AuditLog.action.not_like("WS\\_%", escape="\\"))
                .where(AuditLog.action != "SYSTEM_JOB_RUN")
                .order_by(column)
                .limit(200)
            )
            values.append(list(await self._session.scalars(query)))
        return AuditFilterChoices(actions=values[0], entities=values[1])

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
        if actor_category not in {"ADMIN", "CUSTOMER", "COURIER", "SYSTEM"}:
            if actor_user_id is None:
                actor_category = "SYSTEM"
            else:
                role = await self._session.scalar(select(User.role).where(User.id == actor_user_id))
                if role is None:
                    raise ValueError("Audit actor was not found.")
                actor_category = role.value
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
        actor_categories: tuple[str, ...] | None = None,
        before_at: datetime | None = None,
        before_id: uuid.UUID | None = None,
        limit: int = 50,
        activity_id: uuid.UUID | None = None,
        activity_name: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        oldest_first: bool = False,
    ) -> list[AuditLog]:
        """Return recent audit rows, newest first, with optional filters."""
        query = (
            select(AuditLog)
            .where(AuditLog.action.not_like("HTTP\\_%", escape="\\"))
            .where(AuditLog.action.not_like("WS\\_%", escape="\\"))
            .where(AuditLog.action != "SYSTEM_JOB_RUN")
            .order_by(
                AuditLog.created_at.asc() if oldest_first else AuditLog.created_at.desc(),
                AuditLog.id.asc() if oldest_first else AuditLog.id.desc(),
            )
            .limit(max(1, min(limit, 101)))
        )
        for column, value in (
            (AuditLog.actor_user_id, actor_user_id),
            (AuditLog.id, activity_id),
            (AuditLog.action, action),
            (AuditLog.entity_type, entity_type),
        ):
            if value is not None:
                query = query.where(column == value)
        if activity_name:
            escaped = activity_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(AuditLog.action.ilike(f"%{escaped}%", escape="\\"))
        if start_at is not None:
            query = query.where(AuditLog.created_at >= database_datetime(start_at))
        if end_at is not None:
            query = query.where(AuditLog.created_at <= database_datetime(end_at))
        category = AuditLog.audit_metadata.op("->>")(literal_column("'actor_category'"))
        if actor_categories is not None:
            query = query.where(category.in_(actor_categories))
        elif actor_category is not None:
            query = query.where(category == actor_category)
        if before_at is not None and before_id is not None:
            cursor = tuple_(AuditLog.created_at, AuditLog.id)
            query = query.where(
                cursor > (before_at, before_id) if oldest_first else cursor < (before_at, before_id)
            )
        return list(await self._session.scalars(query))
