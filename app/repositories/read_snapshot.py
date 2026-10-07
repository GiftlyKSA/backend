"""Retain small lookup results for one read-only request transaction."""

from dataclasses import dataclass, field
from typing import TypeVar, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransaction

from app.models.base import Base

Row = TypeVar("Row", bound=Base)


@dataclass
class _Snapshot:
    transaction: SessionTransaction
    rows: dict[tuple[type[Base], UUID], Base | None] = field(default_factory=dict)


async def get_read_row(session: AsyncSession, model: type[Row], row_id: UUID) -> Row | None:
    """Reuse reads only within the current explicitly read-only transaction."""
    if session.info.get("read_only_request") is not True:
        return await session.get(model, row_id)
    transaction = session.sync_session.get_transaction()
    snapshot = session.info.get("read_snapshot")
    key = (model, row_id)
    if isinstance(snapshot, _Snapshot) and snapshot.transaction is transaction:
        if key in snapshot.rows:
            return cast("Row | None", snapshot.rows[key])
    row = await session.get(model, row_id)
    transaction = session.sync_session.get_transaction()
    if transaction is not None:
        if not isinstance(snapshot, _Snapshot) or snapshot.transaction is not transaction:
            snapshot = _Snapshot(transaction)
            session.info["read_snapshot"] = snapshot
        snapshot.rows[key] = row
    return row
