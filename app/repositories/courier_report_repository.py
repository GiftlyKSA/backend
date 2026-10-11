"""Bounded SQL aggregates over authoritative courier settlement ledger."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Date, Select, case, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert, ReturningUpdate

from app.core.exceptions import ConflictError
from app.core.money import parse_money
from app.models import Transaction, Wallet
from app.models.courier_report_settings import CourierReportSettings
from app.models.enums import TransactionStatus, TransactionType, WalletType
from app.schemas.courier_report_schemas import Granularity, TargetUpdate


class CourierReportRepository:
    """Read exact proceeds and atomically replace the owner's target."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the request transaction session."""
        self._session = session

    def _proceeds(
        self, courier_id: uuid.UUID, start: datetime, end: datetime
    ) -> Select[tuple[Decimal]]:
        return (
            select(Transaction.amount)
            .join(Wallet, Transaction.wallet_id == Wallet.id)
            .where(
                Wallet.user_id == courier_id,
                Wallet.type == WalletType.COURIER,
                Transaction.type == TransactionType.ESCROW_RELEASE,
                Transaction.status == TransactionStatus.SETTLED,
                Transaction.reference_order_id.is_not(None),
                Transaction.reference_invoice_id.is_not(None),
                Transaction.created_at >= start,
                Transaction.created_at < end,
            )
        )

    async def totals(
        self, courier_id: uuid.UUID, start: datetime, end: datetime, previous_start: datetime
    ) -> tuple[Decimal, Decimal]:
        """Aggregate current and previous periods in one bounded query."""
        query = self._proceeds(courier_id, previous_start, end).with_only_columns(
            func.coalesce(
                func.sum(case((Transaction.created_at >= start, Transaction.amount), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Transaction.created_at < start, Transaction.amount), else_=0)), 0
            ),
        )
        row = (await self._session.execute(query)).one()
        return Decimal(row[0]), Decimal(row[1])

    async def total(self, courier_id: uuid.UUID, start: datetime, end: datetime) -> Decimal:
        """Sum signed settled proceeds for the requested period."""
        value = await self._session.scalar(
            self._proceeds(courier_id, start, end).with_only_columns(
                func.coalesce(func.sum(Transaction.amount), 0)
            )
        )
        assert value is not None
        return Decimal(value)

    async def buckets(
        self, courier_id: uuid.UUID, start: datetime, end: datetime, granularity: Granularity
    ) -> dict[date, Decimal]:
        """Group eligible ledger rows into at most 366 local calendar buckets."""
        local = func.timezone("Asia/Riyadh", Transaction.created_at)
        bucket = cast(func.date_trunc(granularity, local), Date)
        if granularity == "week":
            bucket = cast(
                func.date_trunc("week", local + timedelta(days=1)) - timedelta(days=1), Date
            )
        query = (
            self._proceeds(courier_id, start, end)
            .with_only_columns(bucket, func.sum(Transaction.amount))
            .group_by(bucket)
        )
        return {row[0]: Decimal(row[1]) for row in (await self._session.execute(query)).all()}

    async def settings(self, courier_id: uuid.UUID) -> CourierReportSettings | None:
        """Read only the authenticated courier owner settings."""
        return await self._session.get(CourierReportSettings, courier_id, populate_existing=True)

    async def replace_settings(
        self, courier_id: uuid.UUID, body: TargetUpdate
    ) -> CourierReportSettings:
        """Replace exactly the observed version or reject the concurrent write."""
        query: (
            ReturningInsert[tuple[CourierReportSettings]]
            | ReturningUpdate[tuple[CourierReportSettings]]
        )
        values = dict(
            enabled=body.target_enabled,
            amount=parse_money(body.target_amount) if body.target_amount else None,
            period=body.target_period,
            version=body.version + 1,
            updated_at=func.now(),
        )
        if body.version == 0:
            query = (
                insert(CourierReportSettings)
                .values(courier_id=courier_id, **values)
                .on_conflict_do_nothing(index_elements=[CourierReportSettings.courier_id])
                .returning(CourierReportSettings)
            )
        else:
            query = (
                update(CourierReportSettings)
                .where(
                    CourierReportSettings.courier_id == courier_id,
                    CourierReportSettings.version == body.version,
                )
                .values(**values)
                .returning(CourierReportSettings)
            )
        row = await self._session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise ConflictError("Report settings changed; refresh and retry.")
        return row
