"""Set transaction-local actor metadata for database change auditing."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def mark_request_transaction(session: AsyncSession) -> None:
    """Require a verified actor before request code changes application data."""
    await session.execute(select(func.set_config("giftly.audit_origin", "request", True)))


async def set_audit_actor(
    session: AsyncSession, *, category: str, actor_user_id: uuid.UUID | None
) -> None:
    """Bind a verified role and optional user to the current transaction only."""
    if category not in {"CUSTOMER", "COURIER", "ADMIN", "SYSTEM"}:
        raise ValueError("Invalid audit actor category.")
    session.info["audit_actor_category"] = category
    session.info["audit_actor_id"] = actor_user_id
    if session.info.get("read_only_request") is True:
        return
    await session.execute(
        select(
            func.set_config("giftly.audit_category", category, True),
            func.set_config(
                "giftly.audit_actor_id", str(actor_user_id) if actor_user_id else "", True
            ),
        )
    )
