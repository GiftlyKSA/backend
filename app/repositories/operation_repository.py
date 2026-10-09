"""Bounded PostgreSQL claims and ownership reads for write recovery."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Occasion, Order, PaymentIntent, WriteOperation


class OperationRepository:
    """Persist operation records in the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Use the caller's database transaction."""
        self.session = session

    async def get(self, owner: UUID, operation: str, key: UUID) -> WriteOperation | None:
        """Select only this account's operation key."""
        row: WriteOperation | None = await self.session.scalar(
            select(WriteOperation).where(
                WriteOperation.user_id == owner,
                WriteOperation.operation == operation,
                WriteOperation.operation_key == key,
            )
        )

        return row

    async def claim(
        self, owner: UUID, operation: str, key: UUID, request_hash: str, expires_at: datetime
    ) -> tuple[WriteOperation, bool]:
        """Let the unique constraint serialize concurrent retries."""
        row = await self.session.scalar(
            insert(WriteOperation)
            .values(
                user_id=owner,
                operation=operation,
                operation_key=key,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_write_operation_key")
            .returning(WriteOperation)
        )
        if row is not None:
            return row, True
        existing = await self.get(owner, operation, key)
        if existing is None:
            raise RuntimeError("Operation claim disappeared during conflict resolution.")
        return existing, False

    async def save(self, row: WriteOperation) -> None:
        """Flush result or linkage changes in the caller's transaction."""
        await self.session.flush()

    async def resource_accessible(self, row: WriteOperation) -> bool:
        """Recheck current resource ownership before replay."""
        if row.resource_id is None:
            return True
        if row.operation == "order.create":
            query = select(Order.id).where(
                Order.id == row.resource_id, Order.customer_id == row.user_id
            )
        elif row.operation.startswith("occasion."):
            query = select(Occasion.id).where(
                Occasion.id == row.resource_id, Occasion.user_id == row.user_id
            )
        else:
            query = select(PaymentIntent.id).where(
                PaymentIntent.id == row.resource_id, PaymentIntent.user_id == row.user_id
            )
        return await self.session.scalar(query) is not None

    async def purge(self, before: datetime, limit: int = 1000) -> int:
        """Delete a bounded batch of expired completed snapshots."""
        ids = (
            select(WriteOperation.id)
            .where(WriteOperation.expires_at < before, WriteOperation.result_encrypted.is_not(None))
            .order_by(WriteOperation.expires_at, WriteOperation.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(
            delete(WriteOperation).where(WriteOperation.id.in_(ids)).returning(WriteOperation.id)
        )
        return len(result.scalars().all())
