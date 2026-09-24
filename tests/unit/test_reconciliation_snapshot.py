"""Reconciliation report and single-statement query regressions."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.repositories.wallet_repository import WalletRepository
from app.services.money_service import MoneyService
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.parametrize("wallet_count,correlation_count", [(0, 0), (4, 0), (100_000, 90_000)])
async def test_clean_snapshot_returns_counts_without_wallet_rows(
    wallet_count: int, correlation_count: int
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = [(wallet_count, correlation_count, None, None, None, None)]

    report = await MoneyService(WalletRepository(session)).reconcile()

    assert report.ok
    assert report.wallets_checked == wallet_count
    assert report.correlations_checked == correlation_count
    session.execute.assert_awaited_once()
    session.scalars.assert_not_awaited()
    session.scalar.assert_not_awaited()


async def test_snapshot_preserves_exact_drift_strings_and_counts() -> None:
    wallet_id, correlation_id = UUID(int=1), UUID(int=2)
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = [
        (9, 3, wallet_id, Decimal("9000000000.01"), Decimal("9000000000.00"), None),
        (9, 3, None, None, Decimal("-0.01"), correlation_id),
    ]

    report = await MoneyService(WalletRepository(session)).reconcile()

    assert not report.ok
    assert (report.wallets_checked, report.correlations_checked) == (9, 3)
    assert report.drifts == [
        f"wallet {wallet_id} balance 9000000000.01 != settled sum 9000000000.00",
        f"correlation {correlation_id} settled sum -0.01 != 0.00",
    ]


async def test_correlation_only_drift_keeps_all_counts() -> None:
    correlation_id = UUID(int=3)
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = [(4, 7, None, None, Decimal("10.00"), correlation_id)]

    report = await MoneyService(WalletRepository(session)).reconcile()

    assert (report.wallets_checked, report.correlations_checked) == (4, 7)
    assert report.drifts == [f"correlation {correlation_id} settled sum 10.00 != 0.00"]


async def test_reconciliation_sql_filters_discrepancies_and_counts_in_one_statement() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = [(0, 0, None, None, None, None)]
    await MoneyService(WalletRepository(session)).reconcile()

    session.execute.assert_awaited_once()
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "UNION ALL" in sql
    assert "LEFT OUTER JOIN" in sql
    assert "GROUP BY transactions.wallet_id" in sql
    assert "GROUP BY transactions.correlation_id" in sql
    assert "wallets.balance != coalesce(" in sql
    assert "correlation_sums.total !=" in sql
    assert sql.count("transactions.status =") == 2
    assert "count(wallets.id)" in sql
    assert "count(*)" in sql
    assert "wallets.user_id" not in sql
    assert "FOR UPDATE" not in sql
    assert "SETTLED" in compiled.params.values()
