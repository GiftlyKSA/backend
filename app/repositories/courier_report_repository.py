"""Bounded SQL aggregates over authoritative courier settlement ledger."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Select,
    case,
    cast,
    func,
    literal,
    null,
    select,
    tuple_,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert, ReturningUpdate

from app.core.exceptions import ConflictError
from app.core.money import ZERO, parse_money
from app.models import Order, Transaction, Wallet
from app.models.courier_report_settings import CourierReportSettings
from app.models.enums import (
    OrderStatus,
    TransactionStatus,
    TransactionType,
    WalletType,
)
from app.schemas.courier_report_schemas import Granularity, TargetUpdate

CURRENT_COURIER_ORDER_STATUSES = (
    OrderStatus.ASSIGNED,
    OrderStatus.WAITING_PAYMENT,
    OrderStatus.IN_PROGRESS,
    OrderStatus.DELIVERED,
    OrderStatus.DISPUTED,
)


@dataclass(frozen=True, slots=True)
class CourierSummaryAggregate:
    """Database snapshot for courier homepage counts and proceeds."""

    current_orders_count: int
    registered_orders_count: int
    completed_orders_count: int
    settled_earnings_total: Decimal
    currency: str
    as_of: datetime


@dataclass(frozen=True, slots=True)
class CourierEarningsAggregate:
    """Current and previous proceeds, current chart buckets, and optional target total."""

    current: Decimal
    previous: Decimal
    buckets: dict[date, Decimal]
    target: Decimal | None = None


class CourierReportRepository:
    """Read exact proceeds and atomically replace the owner's target."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the request transaction session."""
        self._session = session

    def _proceeds(
        self, courier_id: uuid.UUID, start: datetime | None, end: datetime | None
    ) -> Select[Decimal]:
        query = (
            select(Transaction.amount)
            .join(Wallet, Transaction.wallet_id == Wallet.id)
            .where(
                Wallet.user_id == courier_id,
                Wallet.type == WalletType.COURIER,
                Transaction.type == TransactionType.ESCROW_RELEASE,
                Transaction.status == TransactionStatus.SETTLED,
                Transaction.reference_order_id.is_not(None),
                Transaction.reference_invoice_id.is_not(None),
            )
        )
        if start is not None:
            query = query.where(Transaction.created_at >= start)
        if end is not None:
            query = query.where(Transaction.created_at < end)
        return query

    def _summary_query(
        self, courier_id: uuid.UUID
    ) -> Select[int, int, int, Decimal, str, datetime]:
        """Build one owner-scoped statement for homepage counters and proceeds."""
        earnings = (
            self._proceeds(courier_id, None, None)
            .with_only_columns(func.coalesce(func.sum(Transaction.amount), Decimal("0.00")))
            .scalar_subquery()
        )
        return (
            select(
                func.count(Order.id)
                .filter(Order.status.in_(CURRENT_COURIER_ORDER_STATUSES))
                .label("current_orders_count"),
                func.count(Order.id).label("registered_orders_count"),
                func.count(Order.id)
                .filter(Order.status == OrderStatus.COMPLETED)
                .label("completed_orders_count"),
                earnings.label("settled_earnings_total"),
                literal("SAR").label("currency"),
                type_coerce(func.statement_timestamp(), DateTime(timezone=True)).label("as_of"),
            )
            .select_from(Order)
            .where(Order.courier_id == courier_id)
        )

    async def summary(self, courier_id: uuid.UUID) -> CourierSummaryAggregate:
        """Read all homepage metrics in one database snapshot without paging history."""
        query = self._summary_query(courier_id)
        row = (await self._session.execute(query)).one()
        return CourierSummaryAggregate(
            current_orders_count=int(row.current_orders_count or 0),
            registered_orders_count=int(row.registered_orders_count or 0),
            completed_orders_count=int(row.completed_orders_count or 0),
            settled_earnings_total=Decimal(row.settled_earnings_total or Decimal("0.00")),
            currency=row.currency,
            as_of=row.as_of,
        )

    def _period_total(
        self, courier_id: uuid.UUID, start: datetime, end: datetime
    ) -> Select[Decimal]:
        return self._proceeds(courier_id, start, end).with_only_columns(
            func.coalesce(func.sum(Transaction.amount), ZERO)
        )

    async def total(self, courier_id: uuid.UUID, start: datetime, end: datetime) -> Decimal:
        """Sum signed settled proceeds for the requested period."""
        value = await self._session.scalar(self._period_total(courier_id, start, end))
        assert value is not None
        return Decimal(value)

    async def earnings(
        self,
        courier_id: uuid.UUID,
        start: datetime,
        end: datetime,
        previous_start: datetime,
        granularity: Granularity,
        *,
        target_bounds: tuple[datetime, datetime] | None = None,
    ) -> CourierEarningsAggregate:
        """Read the report and optional bounded target from the same ledger snapshot."""
        local = func.timezone("Asia/Riyadh", Transaction.created_at)
        truncated = (
            func.date_trunc("week", local + timedelta(days=1)) - timedelta(days=1)
            if granularity == "week"
            else func.date_trunc(granularity, local)
        )
        in_current = Transaction.created_at >= start
        # GROUPING distinguishes the previous period's NULL bucket from the subtotal.
        bucket = case((in_current, cast(truncated, Date)))
        target = (
            self._period_total(courier_id, *target_bounds).scalar_subquery()
            if target_bounds is not None
            else null()
        )
        query = (
            self._proceeds(courier_id, previous_start, end)
            .with_only_columns(
                bucket,
                func.grouping(bucket),
                func.sum(Transaction.amount),
                func.sum(Transaction.amount).filter(in_current),
                target,
            )
            .group_by(func.grouping_sets(tuple_(bucket), tuple_()))
        )
        current = previous = ZERO
        target_total = None
        buckets: dict[date, Decimal] = {}
        rows = await self._session.execute(query)
        for day, subtotal, amount, current_total, target_amount in rows:
            if subtotal:
                current = Decimal(current_total) if current_total is not None else ZERO
                target_total = Decimal(target_amount) if target_amount is not None else None
            elif day is None:
                previous = Decimal(amount)
            else:
                buckets[day] = Decimal(amount)
        return CourierEarningsAggregate(current, previous, buckets, target_total)

    async def settings(self, courier_id: uuid.UUID) -> CourierReportSettings | None:
        """Read only the authenticated courier owner settings."""
        return await self._session.get(CourierReportSettings, courier_id, populate_existing=True)

    async def replace_settings(
        self, courier_id: uuid.UUID, body: TargetUpdate
    ) -> CourierReportSettings:
        """Replace exactly the observed version or reject the concurrent write."""
        query: ReturningInsert[CourierReportSettings] | ReturningUpdate[CourierReportSettings]
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
