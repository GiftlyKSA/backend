"""Repair unpaid VAT through immutable revisions, never through ledger edits."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.money import ZERO, quantize_money
from app.core.pricing import PricingLine, PricingResult
from app.models import Invoice, InvoiceItem
from app.models.enums import InvoiceStatus, OrderStatus, PromoRedemptionStatus
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.promo_repository import PromoRepository


def corrected_items_only_result(invoice: Invoice, items: list[InvoiceItem]) -> PricingResult:
    """Preserve historical prices and allocations; remove only non-item VAT."""
    lines = [
        PricingLine(
            position=item.position,
            title=item.title,
            description=item.description,
            unit_price_amount=item.unit_price_amount,
            quantity=item.quantity,
            tax_rate=item.tax_rate,
            line_net_amount=item.line_net_amount,
            line_discount_amount=item.line_discount_amount,
            line_taxable_amount=item.line_taxable_amount,
            line_tax_amount=quantize_money(item.line_taxable_amount * item.tax_rate),
            line_total_amount=item.line_taxable_amount
            + quantize_money(item.line_taxable_amount * item.tax_rate),
        )
        for item in items
    ]
    item_discount = sum((line.line_discount_amount for line in lines), ZERO)
    tax = sum((line.line_tax_amount for line in lines), ZERO)
    courier_discount = invoice.discount_amount - item_discount
    if (
        not lines
        or sum((line.line_net_amount for line in lines), ZERO) != invoice.items_net_amount
        or not ZERO <= courier_discount <= invoice.courier_fee_amount
        or invoice.net_after_discount_amount
        != invoice.items_net_amount
        + invoice.courier_fee_amount
        + invoice.service_fee_amount
        - invoice.discount_amount
        or any(
            line.line_taxable_amount != line.line_net_amount - line.line_discount_amount
            or line.line_taxable_amount < ZERO
            or not ZERO <= line.tax_rate <= 1
            for line in lines
        )
    ):
        raise ConflictError("Stored invoice amounts are inconsistent; review manually.")
    breakdown = deepcopy(invoice.pricing_breakdown or {})
    breakdown.update(
        tax_scope="ITEMS_ONLY",
        tax_amount=str(tax),
        total_amount=str(invoice.net_after_discount_amount + tax),
        courier_fee_tax_amount="0.00",
        service_fee_tax_amount="0.00",
    )
    policy = breakdown.get("pricing_policy")
    if isinstance(policy, dict):
        policy["tax_scope"] = "ITEMS_ONLY"
    return PricingResult(
        lines=lines,
        items_net_amount=invoice.items_net_amount,
        courier_fee_amount=invoice.courier_fee_amount,
        courier_fee_discount_amount=courier_discount,
        courier_fee_tax_amount=ZERO,
        service_fee_amount=invoice.service_fee_amount,
        service_fee_tax_amount=ZERO,
        discount_amount=invoice.discount_amount,
        net_after_discount_amount=invoice.net_after_discount_amount,
        tax_amount=tax,
        total_amount=invoice.net_after_discount_amount + tax,
        breakdown=breakdown,
    )


class InvoiceVatRepairService:
    """Apply reviewed corrections under invoice/order/payment locks."""

    def __init__(self, session: AsyncSession) -> None:
        """Share a single transaction with the CLI caller."""
        self.invoices = InvoiceRepository(session)
        self.orders = OrderRepository(session)
        self.payments = PaymentRepository(session)
        self.promos = PromoRepository(session)

    async def repair(self, invoice_id: UUID, *, apply: bool) -> tuple[Invoice, PricingResult]:
        """Preview or replace a still-active, unpaid invoice with no pending payment."""
        invoice = await self.invoices.lock(invoice_id)
        if (
            invoice is None
            or invoice.status is not InvoiceStatus.ISSUED
            or invoice.paid_at is not None
            or invoice.expires_at is None
            or invoice.expires_at <= datetime.now(UTC)
        ):
            raise ConflictError("Only unpaid, unexpired issued invoices can be repaired.")
        order = await self.orders.lock(invoice.order_id)
        active = await self.invoices.get_active_for_order(invoice.order_id)
        if order is None or order.status is not OrderStatus.WAITING_PAYMENT or active != invoice:
            raise ConflictError("The order no longer has this active unpaid invoice.")
        if await self.payments.get_open_intent_for_invoice(invoice.id, for_update=True):
            raise ConflictError("A payment is unresolved; do not change its amount.")
        result = corrected_items_only_result(invoice, await self.invoices.list_items(invoice.id))
        if not apply or (
            result.tax_amount == invoice.tax_amount and result.total_amount == invoice.total_amount
        ):
            return invoice, result
        await self.promos.lock_many([invoice.promo_id] if invoice.promo_id else [])
        redemption = await self.promos.get_redemption_by_invoice(invoice.id)
        if (invoice.promo_id is not None) != (redemption is not None) or (
            redemption is not None
            and (
                redemption.status is not PromoRedemptionStatus.RESERVED
                or redemption.promo_id != invoice.promo_id
                or redemption.order_id != order.id
                or redemption.discount_amount != invoice.discount_amount
            )
        ):
            raise ConflictError("The promo reservation is inconsistent; review manually.")
        invoice.status = InvoiceStatus.CANCELLED
        await self.invoices.flush()
        replacement = await self.invoices.create_draft(
            order_id=invoice.order_id,
            courier_id=invoice.issued_by_courier_id,
            result=result,
            promo_id=invoice.promo_id,
            promo_code_snapshot=invoice.promo_code_snapshot,
        )
        for line in result.lines:
            await self.invoices.add_item(invoice_id=replacement.id, line=line)
        if redemption is not None:
            await self.promos.set_redemption_status(
                redemption, PromoRedemptionStatus.RELEASED, datetime.now(UTC)
            )
            await self.promos.insert_redemption(
                promo_id=redemption.promo_id,
                user_id=redemption.user_id,
                invoice_id=replacement.id,
                order_id=order.id,
                discount_amount=redemption.discount_amount,
            )
        if invoice.expires_at <= datetime.now(UTC):
            raise ConflictError("The invoice expired during repair.")
        await self.invoices.issue(
            replacement, issued_at=datetime.now(UTC), expires_at=invoice.expires_at
        )
        order.total_amount = result.total_amount
        await self.invoices.flush()
        return replacement, result
