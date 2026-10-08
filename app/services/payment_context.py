"""Immutable invoice context for hosted payments and customer session details."""

from app.core.money import money_str
from app.models import Invoice, InvoiceItem
from app.schemas.invoices import InvoiceItemResponse, InvoiceResponse


def invoice_snapshot(invoice: Invoice, items: list[InvoiceItem]) -> InvoiceResponse:
    """Preserve complete priced lines, fees and totals even for split payments."""
    return InvoiceResponse(
        id=str(invoice.id),
        order_id=str(invoice.order_id),
        status=str(invoice.status),
        currency=invoice.currency,
        items_net_amount=money_str(invoice.items_net_amount),
        courier_fee_amount=money_str(invoice.courier_fee_amount),
        service_fee_amount=money_str(invoice.service_fee_amount),
        discount_amount=money_str(invoice.discount_amount),
        net_after_discount_amount=money_str(invoice.net_after_discount_amount),
        total_amount=money_str(invoice.total_amount),
        promo_code=invoice.promo_code_snapshot,
        issued_at=invoice.issued_at.isoformat() if invoice.issued_at else None,
        expires_at=invoice.expires_at.isoformat() if invoice.expires_at else None,
        items=[
            InvoiceItemResponse(
                position=item.position,
                title=item.title,
                description=item.description,
                unit_price_amount=money_str(item.unit_price_amount),
                quantity=item.quantity,
                line_net_amount=money_str(item.line_net_amount),
                line_discount_amount=money_str(item.line_discount_amount),
                line_total_amount=money_str(item.line_total_amount),
            )
            for item in items
        ],
    )
