"""Financial calendar statement with current whole-range ledger totals."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.wallets import TransactionPage


class StatementTotals(BaseModel):
    """Status-separated credits/debits and settled net; all decimal strings."""

    settled_credits: str
    settled_debits: str
    settled_net: str
    pending_credits: str
    pending_debits: str
    reversed_credits: str
    reversed_debits: str


class WalletStatement(TransactionPage):
    """Totals and page share a snapshot; later pages refresh current totals."""

    currency: str
    timezone: Literal["Asia/Riyadh"] = "Asia/Riyadh"
    from_date: date
    to_date: date
    as_of: datetime
    totals: StatementTotals
