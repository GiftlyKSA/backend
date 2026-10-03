"""Customer-controlled invoice revisions with immutable financial history."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.core.exceptions import ConflictError, NotFoundError, ValidationDomainError
from app.core.money import ZERO
from app.core.pricing import PricingConfig, PricingItem, calculate_invoice_totals
from app.models import Invoice, InvoiceItem, Order
from app.models.enums import InvoiceStatus, OrderStatus
from app.repositories.invoice_promo_repository import InvoicePromoRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.promo_repository import normalize_code
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.promo_service import PromoService, to_pricing_promo


def stored_pricing_config(invoice: Invoice) -> PricingConfig:
    """Fail closed when the original fee/tax policy was not retained."""
    policy = (invoice.pricing_breakdown or {}).get("pricing_policy")
    if not isinstance(policy, dict):
        raise ConflictError("The original invoice pricing policy is unavailable.")
    try:
        values = {
            name: Decimal(str(policy[name]))
            for name in (
                "service_fee_rate",
                "service_fee_min_amount",
                "service_fee_max_amount",
                "default_vat_rate",
                "max_invoice_amount",
            )
        }
        if not all(value.is_finite() and value >= ZERO for value in values.values()):
            raise ValueError("Invalid stored policy")
        if (
            values["default_vat_rate"] > 1
            or values["service_fee_rate"] > 1
            or values["service_fee_min_amount"] > values["service_fee_max_amount"]
            or values["max_invoice_amount"] <= ZERO
        ):
            raise ValueError("Invalid stored policy bounds")
        return PricingConfig(**values)
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise ConflictError("The original invoice pricing policy is unavailable.") from exc


class InvoicePromoService:
    """Replace unpaid invoices under the existing invoice/order/payment lock order."""

    def __init__(
        self,
        *,
        invoices: InvoiceRepository,
        orders: OrderRepository,
        payments: PaymentRepository,
        promos: PromoService,
        operations: InvoicePromoRepository,
        eligibility: CourierEligibilityService,
    ) -> None:
        """Use collaborators sharing one database session and transaction."""
        self._invoices = invoices
        self._orders = orders
        self._payments = payments
        self._promos = promos
        self._operations = operations
        self._eligibility = eligibility

    async def apply(
        self, *, invoice_id: UUID, customer_id: UUID, code: str | None, key: str
    ) -> tuple[Invoice, list[InvoiceItem]]:
        """Return a revision or replay without changing paid/in-flight references."""
        await self._eligibility.require_customer(customer_id)
        code = normalize_code(code) if code is not None else None
        if code is not None and not 1 <= len(code) <= 32:
            raise ValidationDomainError("Enter a promo code between 1 and 32 characters.")
        await self._operations.lock_key(customer_id, key)
        replay = await self._operations.get(customer_id, key)
        if replay is not None:
            if replay.invoice_id != invoice_id or replay.code != code:
                raise ConflictError("This idempotency key was used for a different request.")
            invoice = await self._invoices.get_for_customer(replay.result_invoice_id, customer_id)
            if invoice is None:
                raise NotFoundError("Invoice not found.")
            return invoice, await self._invoices.list_items(invoice.id)
        invoice, order = await self._current_invoice(invoice_id, customer_id)
        items = await self._invoices.list_items(invoice.id)
        if code == invoice.promo_code_snapshot:
            await self._operations.record(customer_id, key, invoice_id, code, invoice_id)
            return invoice, items
        replacement = await self._replace(invoice, order, items, customer_id, code)
        await self._operations.record(customer_id, key, invoice_id, code, replacement.id)
        return replacement, await self._invoices.list_items(replacement.id)

    async def _current_invoice(self, invoice_id: UUID, customer_id: UUID) -> tuple[Invoice, Order]:
        """Lock and verify ownership, lifecycle and payment eligibility before changes."""
        invoice = await self._invoices.lock_for_customer(invoice_id, customer_id)
        if invoice is None:
            raise NotFoundError("Invoice not found.")
        now = datetime.now(UTC)
        if (
            invoice.status is not InvoiceStatus.ISSUED
            or invoice.expires_at is None
            or invoice.expires_at <= now
        ):
            raise ConflictError("Refresh the current unpaid, unexpired invoice.")
        order = await self._orders.lock(invoice.order_id)
        if order is None or order.customer_id != customer_id:
            raise NotFoundError("Invoice not found.")
        if order.status is not OrderStatus.WAITING_PAYMENT:
            raise ConflictError("This order is not awaiting payment.")
        active = await self._invoices.get_active_for_order(order.id)
        if active is None or active.id != invoice_id:
            raise ConflictError("Refresh the current invoice before applying a promo.")
        if (
            await self._payments.get_open_intent_for_invoice(invoice_id, for_update=True)
            is not None
        ):
            raise ConflictError("A payment is in progress. Promo changes are unavailable.")
        if invoice.expires_at <= datetime.now(UTC):
            raise ConflictError("This invoice has expired.")
        return invoice, order

    async def _replace(
        self,
        invoice: Invoice,
        order: Order,
        items: list[InvoiceItem],
        customer_id: UUID,
        code: str | None,
    ) -> Invoice:
        """Reserve a new price without mutating old financial fields or their expiry."""
        config = stored_pricing_config(invoice)
        lines = [
            PricingItem(
                title=item.title,
                description=item.description,
                unit_price_amount=item.unit_price_amount,
                quantity=item.quantity,
                tax_rate=item.tax_rate,
                position=item.position,
            )
            for item in items
        ]
        async with self._operations.savepoint():
            await self._promos.lock_replacement(code, invoice.promo_id)
            await self._promos.release(invoice_id=invoice.id)
            promo = None
            if code is not None:
                validation = await self._promos.validate(
                    code=code,
                    discountable_base=invoice.items_net_amount + invoice.courier_fee_amount,
                    user_id=customer_id,
                )
                promo = validation.promo
            result = calculate_invoice_totals(
                lines,
                invoice.courier_fee_amount,
                to_pricing_promo(promo) if promo is not None else None,
                config,
            )
            if result.service_fee_amount != invoice.service_fee_amount:
                raise ConflictError("The original invoice pricing policy is inconsistent.")
            if promo is not None and result.discount_amount <= ZERO:
                raise ValidationDomainError("This promo yields no discount for this invoice.")
            invoice.status = InvoiceStatus.CANCELLED
            await self._invoices.flush()
            replacement = await self._invoices.create_draft(
                order_id=invoice.order_id,
                courier_id=invoice.issued_by_courier_id,
                result=result,
                promo_id=promo.id if promo else None,
                promo_code_snapshot=promo.code if promo else None,
            )
            for line in result.lines:
                await self._invoices.add_item(invoice_id=replacement.id, line=line)
            if promo is not None:
                await self._promos.reserve(
                    promo=promo,
                    user_id=customer_id,
                    invoice_id=replacement.id,
                    order_id=order.id,
                    discount_amount=result.discount_amount,
                )
            if invoice.expires_at is None:
                raise ConflictError("Invoice expiry is unavailable.")
            issued_at = datetime.now(UTC)
            if invoice.expires_at <= issued_at:
                raise ConflictError("This invoice has expired.")
            await self._invoices.issue(
                replacement, issued_at=issued_at, expires_at=invoice.expires_at
            )
            order.total_amount = result.total_amount
            await self._invoices.flush()
            return replacement
