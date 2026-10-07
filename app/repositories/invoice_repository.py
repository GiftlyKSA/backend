"""Invoice and invoice-item persistence (SPEC SECTION 11, 14).

Every stored amount is the OUTPUT of the pricing engine; this layer never computes a
price, it only writes what ``core/pricing.py`` produced and reads it back verbatim.
Ownership on reads is enforced in the query (customer or courier of the parent order),
never fetch-then-compare.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import NotFoundError
from app.core.pricing import PricingLine, PricingResult
from app.models import Invoice, InvoiceItem, Order
from app.models.enums import InvoiceStatus, UserRole

# An invoice in one of these statuses blocks a second active invoice for the order
# (mirrors the partial unique index ``uq_invoices_one_active_per_order``).
_ACTIVE_STATUSES = (InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, InvoiceStatus.PAID)


class InvoiceRepository:
    """Creates and reads invoices, with FOR UPDATE locking for pay/cancel."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    async def list_for_actor(
        self,
        actor_id: uuid.UUID,
        *,
        role: UserRole,
        limit: int,
        cursor: uuid.UUID | None,
        status: InvoiceStatus | None,
        include_historical: bool,
        start: datetime | None,
        end: datetime | None,
    ) -> list[tuple[Invoice, bool]]:
        """Filter revisions and ownership before a deterministic keyset page."""
        newer = aliased(Invoice)
        later = select(newer.id).where(
            newer.order_id == Invoice.order_id,
            tuple_(newer.created_at, newer.id) > tuple_(Invoice.created_at, Invoice.id),
        )
        if role is UserRole.CUSTOMER:
            later = later.where(newer.status != InvoiceStatus.DRAFT)
        current = ~exists(later.correlate(Invoice))
        owner = Order.customer_id if role is UserRole.CUSTOMER else Order.courier_id
        query = (
            select(Invoice, current.label("is_current"))
            .join(Order, Order.id == Invoice.order_id)
            .where(owner == actor_id)
        )
        if role is UserRole.CUSTOMER:
            query = query.where(Invoice.status != InvoiceStatus.DRAFT)
        if not include_historical:
            query = query.where(current)
        if status is not None:
            query = query.where(Invoice.status == status)
        if start is not None:
            query = query.where(Invoice.issued_at >= start)
        if end is not None:
            query = query.where(Invoice.issued_at < end)
        if cursor is not None:
            anchor = await self._session.scalar(query.where(Invoice.id == cursor))
            if anchor is None:
                raise NotFoundError("Pagination cursor not found in this list.")
            query = query.where(
                tuple_(Invoice.created_at, Invoice.id)
                < (
                    anchor.created_at,
                    anchor.id,
                )
            )
        rows = await self._session.execute(
            query.order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(limit)
        )
        return [(row[0], bool(row[1])) for row in rows]

    async def list_vat_repair_ids(self, *, after: uuid.UUID | None, limit: int) -> list[uuid.UUID]:
        """Page unpaid candidates without exposing customer data to the repair report."""
        query = select(Invoice.id).where(Invoice.status == InvoiceStatus.ISSUED)
        if after is not None:
            query = query.where(Invoice.id > after)
        return list(await self._session.scalars(query.order_by(Invoice.id).limit(limit)))

    async def create_draft(
        self,
        *,
        order_id: uuid.UUID,
        courier_id: uuid.UUID,
        result: PricingResult,
        promo_id: uuid.UUID | None,
        promo_code_snapshot: str | None,
    ) -> Invoice:
        """Insert a DRAFT invoice from a computed pricing result.

        Items can only be attached while DRAFT (the ``enforce_invoice_item_freeze``
        trigger), so the invoice is created DRAFT, lined, then issued. The amounts are
        copied straight from ``result``; the DB CHECKs re-verify the arithmetic.
        """
        invoice = Invoice(
            order_id=order_id,
            issued_by_courier_id=courier_id,
            status=InvoiceStatus.DRAFT,
            items_net_amount=result.items_net_amount,
            courier_fee_amount=result.courier_fee_amount,
            service_fee_amount=result.service_fee_amount,
            discount_amount=result.discount_amount,
            net_after_discount_amount=result.net_after_discount_amount,
            tax_amount=result.tax_amount,
            total_amount=result.total_amount,
            promo_id=promo_id,
            promo_code_snapshot=promo_code_snapshot,
            pricing_breakdown=result.breakdown,
        )
        self._session.add(invoice)
        await self._session.flush()
        return invoice

    async def issue(self, invoice: Invoice, *, issued_at: datetime, expires_at: datetime) -> None:
        """Transition a fully-lined DRAFT invoice to ISSUED, stamping the deadlines."""
        invoice.status = InvoiceStatus.ISSUED
        invoice.issued_at = issued_at
        invoice.expires_at = expires_at
        await self._session.flush()

    async def add_item(self, *, invoice_id: uuid.UUID, line: PricingLine) -> None:
        """Attach one computed line (a :class:`PricingLine`) to an invoice."""
        await self.add_items(invoice_id=invoice_id, lines=[line])

    async def add_items(self, *, invoice_id: uuid.UUID, lines: Sequence[PricingLine]) -> None:
        """Persist computed lines together while the parent invoice is still DRAFT."""
        self._session.add_all(
            [
                InvoiceItem(
                    invoice_id=invoice_id,
                    position=line.position,
                    title=line.title,
                    description=line.description,
                    unit_price_amount=line.unit_price_amount,
                    quantity=line.quantity,
                    tax_rate=line.tax_rate,
                    line_net_amount=line.line_net_amount,
                    line_discount_amount=line.line_discount_amount,
                    line_taxable_amount=line.line_taxable_amount,
                    line_tax_amount=line.line_tax_amount,
                    line_total_amount=line.line_total_amount,
                )
                for line in lines
            ]
        )
        await self._session.flush()

    async def get(self, invoice_id: uuid.UUID) -> Invoice | None:
        """Return an invoice by id, or None."""
        return await self._session.get(Invoice, invoice_id)

    async def get_for_actor(self, invoice_id: uuid.UUID, actor_id: uuid.UUID) -> Invoice | None:
        """Return an invoice only if the actor is the order's customer or courier."""
        result: Invoice | None = await self._session.scalar(
            select(Invoice)
            .join(Order, Order.id == Invoice.order_id)
            .where(
                Invoice.id == invoice_id,
                (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
            )
        )
        return result

    async def get_active_for_order(self, order_id: uuid.UUID) -> Invoice | None:
        """Return the order's active (DRAFT/ISSUED/PAID) invoice, or None."""
        result: Invoice | None = await self._session.scalar(
            select(Invoice).where(
                Invoice.order_id == order_id, Invoice.status.in_(_ACTIVE_STATUSES)
            )
        )
        return result

    async def get_for_customer(
        self, invoice_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Invoice | None:
        """Scope operation replays to the actual customer, not generic participation."""
        invoice: Invoice | None = await self._session.scalar(
            select(Invoice)
            .join(Order, Order.id == Invoice.order_id)
            .where(Invoice.id == invoice_id, Order.customer_id == customer_id)
            .execution_options(populate_existing=True)
        )
        return invoice

    async def get_active_for_customer(
        self, order_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Invoice | None:
        """Return an active invoice only to the actual customer owner."""
        invoice: Invoice | None = await self._session.scalar(
            select(Invoice)
            .join(Order, Order.id == Invoice.order_id)
            .where(
                Invoice.order_id == order_id,
                Invoice.status.in_(_ACTIVE_STATUSES),
                Order.customer_id == customer_id,
            )
        )
        return invoice

    async def get_active_for_order_for_actor(
        self, order_id: uuid.UUID, actor_id: uuid.UUID
    ) -> Invoice | None:
        """Return the order's active invoice only if the actor participates in the order."""
        result: Invoice | None = await self._session.scalar(
            select(Invoice)
            .join(Order, Order.id == Invoice.order_id)
            .where(
                Invoice.order_id == order_id,
                Invoice.status.in_(_ACTIVE_STATUSES),
                (Order.customer_id == actor_id) | (Order.courier_id == actor_id),
            )
        )
        return result

    async def lock(self, invoice_id: uuid.UUID) -> Invoice | None:
        """Load an invoice FOR UPDATE (pay/cancel serialization)."""
        result: Invoice | None = await self._session.scalar(
            select(Invoice)
            .where(Invoice.id == invoice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def lock_for_courier(
        self, invoice_id: uuid.UUID, courier_id: uuid.UUID
    ) -> Invoice | None:
        """Load an invoice FOR UPDATE only if this courier issued it (ownership in query)."""
        result: Invoice | None = await self._session.scalar(
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.issued_by_courier_id == courier_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def lock_for_customer(
        self, invoice_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Invoice | None:
        """Lock an invoice only through its owning customer's order."""
        result: Invoice | None = await self._session.scalar(
            select(Invoice)
            .join(Order, Order.id == Invoice.order_id)
            .where(Invoice.id == invoice_id, Order.customer_id == customer_id)
            .with_for_update(of=Invoice)
            .execution_options(populate_existing=True)
        )
        return result

    async def list_items(self, invoice_id: uuid.UUID) -> list[InvoiceItem]:
        """Return an invoice's items in render order."""
        return list(
            await self._session.scalars(
                select(InvoiceItem)
                .where(InvoiceItem.invoice_id == invoice_id)
                .order_by(InvoiceItem.position)
            )
        )

    async def list_expired_issued(self, *, now: datetime, limit: int) -> list[Invoice]:
        """Return ISSUED invoices whose expiry has passed (oldest first)."""
        return list(
            await self._session.scalars(
                select(Invoice)
                .where(
                    Invoice.status == InvoiceStatus.ISSUED,
                    Invoice.expires_at.is_not(None),
                    Invoice.expires_at < now,
                )
                .order_by(Invoice.expires_at)
                .limit(limit)
            )
        )

    async def list_receipt_pending(self, limit: int) -> list[Invoice]:
        """Return PAID invoices whose receipt has not been sent, oldest first.

        Backed by the partial index ``idx_invoices_receipt_pending``; the receipt
        sweeper drains this set.
        """
        return list(
            await self._session.scalars(
                select(Invoice)
                .where(
                    Invoice.status == InvoiceStatus.PAID,
                    Invoice.receipt_email_sent_at.is_(None),
                    (Invoice.receipt_claimed_until.is_(None))
                    | (Invoice.receipt_claimed_until <= datetime.now(UTC)),
                )
                .order_by(Invoice.paid_at)
                .limit(limit)
            )
        )

    async def claim_receipt(
        self, invoice_id: uuid.UUID, *, token: uuid.UUID, now: datetime, lease: timedelta
    ) -> Invoice | None:
        """Claim an eligible invoice in a short transaction."""
        invoice = await self.lock(invoice_id)
        if (
            invoice is None
            or invoice.status is not InvoiceStatus.PAID
            or invoice.receipt_email_sent_at is not None
            or (invoice.receipt_claimed_until is not None and invoice.receipt_claimed_until > now)
        ):
            return None
        invoice.receipt_claim_token = token
        invoice.receipt_claimed_until = now + lease
        await self._session.flush()
        return invoice

    async def complete_receipt(
        self, invoice_id: uuid.UUID, *, token: uuid.UUID, when: datetime
    ) -> bool:
        """Stamp only the still-owned claim; stale workers cannot complete it."""
        invoice = await self.lock(invoice_id)
        if (
            invoice is None
            or invoice.receipt_claim_token != token
            or invoice.receipt_email_sent_at is not None
        ):
            return False
        invoice.receipt_email_sent_at = when
        invoice.receipt_claim_token = None
        invoice.receipt_claimed_until = None
        await self._session.flush()
        return True

    async def flush(self) -> None:
        """Flush pending writes."""
        await self._session.flush()
