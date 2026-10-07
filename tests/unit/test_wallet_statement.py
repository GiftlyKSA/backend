from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.exceptions import NotFoundError
from app.repositories.wallet_repository import WalletRepository
from app.services.wallet_statement_service import WalletStatementService
from sqlalchemy.dialects import postgresql


async def test_statement_missing_owned_wallet_returns_404():
    repo = AsyncMock()
    repo.get_by_user.return_value = None
    with pytest.raises(NotFoundError):
        await WalletStatementService(repo, AsyncMock()).read(
            uuid4(),
            from_date=date(2026, 10, 1),
            to_date=date(2026, 10, 31),
            limit=25,
            cursor=None,
        )
    repo.statement.assert_not_awaited()


async def test_statement_totals_are_server_money_and_pages_overfetch():
    repo = AsyncMock()
    wallet = SimpleNamespace(id=uuid4(), currency="SAR")
    repo.get_by_user.return_value = wallet
    now = datetime(2026, 10, 7, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=uuid4(),
            amount=Decimal("10.00"),
            type="TOPUP",
            status="SETTLED",
            balance_after=Decimal("10.00"),
            created_at=now,
            description=None,
            reference_order_id=None,
            reference_invoice_id=None,
            reference_intent_id=None,
        )
        for _ in range(3)
    ]
    repo.statement.return_value = SimpleNamespace(
        items=rows,
        as_of=now,
        totals={
            "settled_credits": Decimal("100.00"),
            "settled_debits": Decimal("20.00"),
            "pending_credits": Decimal("50.00"),
            "pending_debits": Decimal("0.00"),
            "reversed_credits": Decimal("0.00"),
            "reversed_debits": Decimal("10.00"),
        },
    )
    result = await WalletStatementService(repo, AsyncMock()).read(
        uuid4(),
        from_date=date(2026, 10, 1),
        to_date=date(2026, 10, 31),
        limit=2,
        cursor=None,
    )
    assert result.totals.settled_net == "80.00"
    assert result.totals.pending_credits == "50.00"
    assert len(result.items) == 2 and result.next_cursor == str(rows[1].id)
    assert repo.statement.call_args.kwargs["start"] == datetime(2026, 9, 30, 21, tzinfo=UTC)
    assert repo.statement.call_args.kwargs["limit"] == 3


async def test_statement_cursor_uses_same_owned_range_before_pagination():
    session = AsyncMock()
    session.scalar.return_value = None
    with pytest.raises(NotFoundError):
        await WalletRepository(session).statement(
            uuid4(),
            start=datetime(2026, 10, 1, tzinfo=UTC),
            end=datetime(2026, 11, 1, tzinfo=UTC),
            limit=26,
            cursor=uuid4(),
        )
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "transactions.wallet_id" in sql
    assert "transactions.created_at >=" in sql
    assert "transactions.created_at <" in sql
    session.execute.assert_not_awaited()


async def test_statement_totals_and_page_use_one_database_snapshot():
    session = AsyncMock()
    session.execute.return_value = [(None, *(Decimal("0.00") for _ in range(6)), datetime.now(UTC))]
    result = await WalletRepository(session).statement(
        uuid4(),
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 11, 1, tzinfo=UTC),
        limit=26,
        cursor=None,
    )
    assert result.items == []
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FILTER" in sql and "LEFT OUTER JOIN" in sql and "LIMIT" in sql
    session.execute.assert_awaited_once()
