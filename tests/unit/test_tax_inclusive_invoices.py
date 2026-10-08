"""Courier item prices are final amounts; the platform adds no tax."""

from decimal import Decimal

import pytest
from app.core.pricing import PricingConfig, PricingItem, calculate_invoice_totals
from app.models import Invoice, InvoiceItem
from app.schemas.invoices import InvoiceLineRequest, InvoiceResponse
from pydantic import ValidationError


def test_invoice_input_rejects_separate_tax_rate():
    with pytest.raises(ValidationError):
        InvoiceLineRequest(title="Gift", unit_price_amount="115.00", quantity=1, tax_rate="0.15")


def test_prices_are_final_and_fees_are_preserved():
    config = PricingConfig(
        service_fee_rate=Decimal("0.05"),
        service_fee_min_amount=Decimal("5.00"),
        service_fee_max_amount=Decimal("500.00"),
        max_invoice_amount=Decimal("50000.00"),
    )
    result = calculate_invoice_totals(
        [PricingItem("Gift", Decimal("115.00"), 2)], Decimal("20.00"), None, config
    )
    assert result.items_net_amount == Decimal("230.00")
    assert result.service_fee_amount == Decimal("12.50")
    assert result.total_amount == Decimal("262.50")
    assert result.lines[0].line_total_amount == Decimal("230.00")
    assert not hasattr(result, "tax_amount")


def test_tax_fields_are_absent_from_contract_and_database_models():
    assert "tax_amount" not in InvoiceResponse.model_fields
    assert "tax_amount" not in Invoice.__table__.c
    assert not {"tax_rate", "line_tax_amount", "line_taxable_amount"} & set(
        InvoiceItem.__table__.c.keys()
    )
