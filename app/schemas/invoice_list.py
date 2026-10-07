"""Compact owned invoice rows without loading invoice items."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import InvoiceStatus


class InvoiceSummary(BaseModel):
    """One visible invoice revision and its current-revision marker."""

    id: UUID
    order_id: UUID
    status: InvoiceStatus
    currency: str
    total_amount: str
    issued_at: datetime | None
    expires_at: datetime | None
    is_current: bool


class InvoicePage(BaseModel):
    """Newest-created invoices first, with an owned UUID cursor."""

    items: list[InvoiceSummary]
    next_cursor: str | None
