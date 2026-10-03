"""VAT is collected on discounted invoice items only."""

from decimal import Decimal

from app.core.pricing import PricingItem, calculate_invoice_totals

from tests.unit.test_pricing import CFG, WELCOME10, _golden_items


def test_fees_have_zero_vat():
    result = calculate_invoice_totals(_golden_items(), Decimal("100"), WELCOME10, CFG)
    assert result.courier_fee_tax_amount == Decimal("0")
    assert result.service_fee_tax_amount == Decimal("0")
    assert result.tax_amount == Decimal("67.50")
    assert result.total_amount == Decimal("637.50")


def test_zero_rated_items_do_not_tax_fees():
    result = calculate_invoice_totals(
        [PricingItem("Gift", Decimal("100"), 1, Decimal("0"))], Decimal("20"), None, CFG
    )
    assert result.tax_amount == Decimal("0")
