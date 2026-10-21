"""Courier reporting calendar, eligibility and target business rules."""

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.exceptions import ValidationDomainError
from app.core.money import ZERO, money_str
from app.repositories.courier_report_repository import CourierReportRepository
from app.schemas.courier_report_schemas import (
    Comparison,
    CourierHomeSummary,
    EarningsBucket,
    EarningsReport,
    Granularity,
    PreviousPeriod,
    TargetProgress,
    TargetSettings,
    TargetUpdate,
)
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.reporting_dates import reporting_bounds

RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")


def bucket_start(day: date, granularity: Granularity) -> date:
    """Return a day, Sunday or first-of-month bucket anchor."""
    if granularity == "week":
        return day - timedelta(days=(day.weekday() + 1) % 7)
    if granularity == "month":
        return day.replace(day=1)
    return day


def next_bucket(day: date, granularity: Granularity) -> date:
    """Return the next bucket boundary."""
    if granularity == "month":
        return (
            day.replace(year=day.year + 1, month=1)
            if day.month == 12
            else day.replace(month=day.month + 1)
        )
    return day + timedelta(days=7 if granularity == "week" else 1)


class CourierReportService:
    """Expose historical proceeds independently of mutable wallet balances."""

    def __init__(
        self, reports: CourierReportRepository, eligibility: CourierEligibilityService
    ) -> None:
        """Wire exact reporting and current eligibility boundaries."""
        self._reports = reports
        self._eligibility = eligibility

    async def settings(self, courier_id: uuid.UUID) -> TargetSettings:
        """Read current target and complete-period progress."""
        await self._eligibility.require_courier(courier_id)
        return await self._target(courier_id)

    async def summary(self, courier_id: uuid.UUID) -> CourierHomeSummary:
        """Return eligible courier's all-time homepage aggregates."""
        await self._eligibility.require_courier(courier_id)
        aggregate = await self._reports.summary(courier_id)
        as_of = aggregate.as_of
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        return CourierHomeSummary(
            current_orders_count=aggregate.current_orders_count,
            registered_orders_count=aggregate.registered_orders_count,
            completed_orders_count=aggregate.completed_orders_count,
            settled_earnings_total=money_str(aggregate.settled_earnings_total),
            currency=aggregate.currency,
            as_of=as_of.astimezone(UTC),
        )

    async def update_settings(self, courier_id: uuid.UUID, body: TargetUpdate) -> TargetSettings:
        """Apply an owner-scoped optimistic target replacement."""
        await self._eligibility.require_courier(courier_id)
        row = await self._reports.settings(courier_id)
        enabled = bool(row and row.enabled)
        amount = money_str(row.amount) if row and row.amount is not None else None
        period = row.period if row else None
        if "target_enabled" in body.model_fields_set:
            enabled = bool(body.target_enabled)
        if "target_amount" in body.model_fields_set:
            amount = body.target_amount
        if "target_period" in body.model_fields_set:
            period = body.target_period
        if enabled and (amount is None or period is None):
            raise ValidationDomainError("Enabled targets require amount and period.")
        replacement = TargetUpdate.model_validate(
            {
                "version": body.version,
                "target_enabled": enabled,
                "target_amount": amount if enabled else None,
                "target_period": period if enabled else None,
            }
        )
        await self._reports.replace_settings(courier_id, replacement)
        return await self._target(courier_id)

    async def _target(self, courier_id: uuid.UUID) -> TargetSettings:
        row = await self._reports.settings(courier_id)
        if row is None or not row.enabled:
            return TargetSettings(
                version=row.version if row else 0,
                updated_at=row.updated_at if row else None,
                target_enabled=False,
                target_amount=None,
                target_period=None,
                progress=None,
            )
        assert row.amount is not None and row.period in {"WEEK", "MONTH"}
        granularity: Granularity = "week" if row.period == "WEEK" else "month"
        today = datetime.now(UTC).astimezone(RIYADH).date()
        first = bucket_start(today, granularity)
        last = next_bucket(first, granularity) - timedelta(days=1)
        start, end = reporting_bounds(first, last)
        assert start is not None and end is not None
        amount = await self._reports.total(courier_id, start, end)
        return TargetSettings(
            version=row.version,
            updated_at=row.updated_at,
            target_enabled=True,
            target_amount=money_str(row.amount),
            target_period="WEEK" if row.period == "WEEK" else "MONTH",
            progress=TargetProgress(
                period="WEEK" if row.period == "WEEK" else "MONTH",
                amount=money_str(row.amount),
                period_start=first,
                period_end=last,
                settled_total=money_str(amount),
                progress_percent=money_str(amount / row.amount * Decimal(100)),
            ),
        )

    async def earnings(
        self, courier_id: uuid.UUID, from_date: date, to_date: date, granularity: Granularity
    ) -> EarningsReport:
        """Compare at most 366 Gregorian days with the preceding equally long range."""
        days = (to_date - from_date).days + 1
        if days < 1 or days > 366 or from_date.toordinal() <= days + 7 or to_date.year == 9999:
            raise ValidationDomainError(
                "Use an ordered Gregorian range of at most 366 days away from calendar extremes."
            )
        await self._eligibility.require_courier(courier_id)
        previous_from = from_date - timedelta(days=days)
        previous_to = from_date - timedelta(days=1)
        start, end = reporting_bounds(from_date, to_date)
        previous_start, _ = reporting_bounds(previous_from, previous_to)
        assert start is not None and end is not None and previous_start is not None
        total, previous = await self._reports.totals(courier_id, start, end, previous_start)
        amounts = await self._reports.buckets(courier_id, start, end, granularity)
        buckets = []
        first = bucket_start(from_date, granularity)
        while first <= to_date:
            following = next_bucket(first, granularity)
            buckets.append(
                EarningsBucket(
                    from_date=max(first, from_date),
                    to_date=min(following - timedelta(days=1), to_date),
                    amount=money_str(amounts.get(first, ZERO)),
                )
            )
            first = following
        comparison: Comparison = "UNCHANGED"
        if total != previous:
            if previous == ZERO:
                comparison = "NO_BASELINE"
            elif total > previous:
                comparison = "INCREASE"
            else:
                comparison = "DECREASE"
        return EarningsReport(
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
            settled_total=money_str(total),
            previous=PreviousPeriod(
                from_date=previous_from,
                to_date=previous_to,
                settled_total=money_str(previous),
                difference=money_str(total - previous),
                percentage=money_str((total - previous) / previous * Decimal(100))
                if previous != ZERO
                else None,
                comparison=comparison,
            ),
            buckets=buckets,
            target=(await self._target(courier_id)).progress,
        )
