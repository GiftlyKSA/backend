"""Account-wide invoice reads with current eligibility and revision semantics."""

from datetime import date
from uuid import UUID

from app.core.money import money_str
from app.models.enums import InvoiceStatus, UserRole
from app.repositories.invoice_repository import InvoiceRepository
from app.schemas.invoice_list import InvoicePage, InvoiceSummary
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.reporting_dates import reporting_bounds


class MobileInvoiceService:
    """Read bounded invoice summaries without duplicating invoice pricing rules."""

    def __init__(self, invoices: InvoiceRepository, eligibility: CourierEligibilityService) -> None:
        """Wire persistence and the existing account boundary."""
        self._invoices = invoices
        self._eligibility = eligibility

    async def list(
        self,
        actor_id: UUID,
        *,
        role: UserRole,
        limit: int,
        cursor: UUID | None,
        status: InvoiceStatus | None,
        include_historical: bool,
        from_date: date | None,
        to_date: date | None,
    ) -> InvoicePage:
        """List the actor's visible revisions, checking eligibility before querying."""
        await self._eligibility.require_marketplace_actor(actor_id)
        start, end = reporting_bounds(from_date, to_date)
        rows = await self._invoices.list_for_actor(
            actor_id,
            role=role,
            limit=limit + 1,
            cursor=cursor,
            status=status,
            include_historical=include_historical,
            start=start,
            end=end,
        )
        items = [
            InvoiceSummary(
                id=row.id,
                order_id=row.order_id,
                status=row.status,
                currency=row.currency,
                total_amount=money_str(row.total_amount),
                issued_at=row.issued_at,
                expires_at=row.expires_at,
                is_current=current,
            )
            for row, current in rows[:limit]
        ]
        return InvoicePage(
            items=items,
            next_cursor=str(items[-1].id) if len(rows) > limit else None,
        )
