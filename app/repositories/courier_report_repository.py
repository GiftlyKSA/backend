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
    select,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningInsert, ReturningUpdate

from app.core.exceptions import ConflictError
from app.core.money import parse_money
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
