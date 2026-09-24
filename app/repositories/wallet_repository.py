"""Wallet and ledger persistence (SPEC SECTION 10, 8.11).

Money writes lock wallets with ``SELECT ... FOR UPDATE`` in ASCENDING wallet-id order
to prevent deadlocks. The ledger is append-only; this repository only ever INSERTs
transactions (never updates/deletes them, except the trigger-permitted PENDING ->
SETTLED|REVERSED status change handled elsewhere).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Numeric, Uuid, cast, func, null, select, true, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.core.money import ZERO
from app.models import Transaction, Wallet
from app.models.enums import TransactionStatus, TransactionType, WalletType


@dataclass
class ReconciliationSnapshot:
    """Counts and discrepancies read from one database snapshot."""

    wallets_checked: int = 0
    correlations_checked: int = 0
    wallet_drifts: list[tuple[uuid.UUID, Decimal, Decimal]] = field(default_factory=list)
    correlation_drifts: list[tuple[uuid.UUID, Decimal]] = field(default_factory=list)


class WalletRepository:
    """Reads wallets, locks them for money writes, and appends ledger rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    def savepoint(self) -> AsyncSessionTransaction:
        """Open a SAVEPOINT so a caller can survive a constraint race (audit MON-1)."""
        return self._session.begin_nested()

    async def get_by_user(self, user_id: uuid.UUID) -> Wallet | None:
        """Return a user's wallet, or None."""
        result: Wallet | None = await self._session.scalar(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        return result

    async def get_system(self, wallet_type: WalletType) -> Wallet:
        """Return a seeded system wallet by type.

        Raises:
            LookupError: The system wallet is missing (should be impossible post-seed).
        """
        wallet = await self._session.scalar(
            select(Wallet).where(Wallet.type == wallet_type, Wallet.user_id.is_(None))
        )
        if wallet is None:
            raise LookupError(f"System wallet {wallet_type} is not seeded.")
        return wallet

    async def lock_wallets(self, wallet_ids: list[uuid.UUID]) -> dict[uuid.UUID, Wallet]:
        """Lock the given wallets FOR UPDATE in ascending id order; return them by id."""
        ordered = sorted(set(wallet_ids))
        rows = await self._session.scalars(
            select(Wallet)
            .where(Wallet.id.in_(ordered))
            .order_by(Wallet.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {w.id: w for w in rows}

    def append_transaction(
        self,
        *,
        wallet_id: uuid.UUID,
        amount: Decimal,
        txn_type: TransactionType,
        status: TransactionStatus,
        correlation_id: uuid.UUID,
        balance_after: Decimal,
        idempotency_key: str | None = None,
        reference_order_id: uuid.UUID | None = None,
        reference_invoice_id: uuid.UUID | None = None,
        reference_intent_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> Transaction:
        """Append one ledger row (not flushed until the caller flushes)."""
        txn = Transaction(
            wallet_id=wallet_id,
            amount=amount,
            type=txn_type,
            status=status,
            correlation_id=correlation_id,
            balance_after=balance_after,
            idempotency_key=idempotency_key,
            reference_order_id=reference_order_id,
            reference_invoice_id=reference_invoice_id,
            reference_intent_id=reference_intent_id,
            description=description,
        )
        self._session.add(txn)
        return txn

    async def flush(self) -> None:
        """Flush pending writes so a subsequent FOR UPDATE reload cannot discard them."""
        await self._session.flush()

    async def idempotency_key_exists(self, key: str) -> bool:
        """Return whether a ledger row with this idempotency key already exists."""
        found = await self._session.scalar(
            select(Transaction.id).where(Transaction.idempotency_key == key)
        )
        return found is not None

    async def settled_balance(self, wallet_id: uuid.UUID) -> Decimal:
        """Return the sum of SETTLED transaction amounts for a wallet."""
        total = await self._session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.wallet_id == wallet_id,
                Transaction.status == TransactionStatus.SETTLED,
            )
        )
        return Decimal(total if total is not None else 0)

    async def list_transactions(
        self, wallet_id: uuid.UUID, *, limit: int, before_id: uuid.UUID | None = None
    ) -> list[Transaction]:
        """Return a wallet's transactions, newest first, keyset-paginated by id.

        Keyset (not OFFSET) pagination: pass the last row's id as ``before_id`` to fetch
        the next page. The ``(wallet_id, created_at DESC, id DESC)`` index serves this.
        """
        query = (
            select(Transaction)
            .where(Transaction.wallet_id == wallet_id)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            anchor = await self._session.get(Transaction, before_id)
            if anchor is not None:
                query = (
                    select(Transaction)
                    .where(
                        Transaction.wallet_id == wallet_id,
                        tuple_(Transaction.created_at, Transaction.id)
                        < (anchor.created_at, anchor.id),
                    )
                    .order_by(Transaction.created_at.desc(), Transaction.id.desc())
                    .limit(limit)
                )
        return list(await self._session.scalars(query))

    async def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        """Read counts and only discrepancies in one READ COMMITTED statement snapshot.

        Worker memory is O(discrepancies), not O(wallets or ledger groups). Both
        persisted money columns are NUMERIC with scale 2, so SQL equality preserves
        the money primitive's comparison without rounding or float conversion.
        """
        wallet_sums = (
            select(Transaction.wallet_id, func.sum(Transaction.amount).label("total"))
            .where(Transaction.status == TransactionStatus.SETTLED)
            .group_by(Transaction.wallet_id)
            .subquery("wallet_sums")
        )
        correlation_sums = (
            select(Transaction.correlation_id, func.sum(Transaction.amount).label("total"))
            .where(Transaction.status == TransactionStatus.SETTLED)
            .group_by(Transaction.correlation_id)
            .cte("correlation_sums")
        )
        settled = func.coalesce(wallet_sums.c.total, ZERO)
        discrepancies = union_all(
            select(
                Wallet.id.label("wallet_id"),
                Wallet.balance,
                settled.label("total"),
                cast(null(), Uuid).label("correlation_id"),
            )
            .outerjoin(wallet_sums, Wallet.id == wallet_sums.c.wallet_id)
            .where(Wallet.balance != settled),
            select(
                cast(null(), Uuid).label("wallet_id"),
                cast(null(), Numeric).label("balance"),
                correlation_sums.c.total,
                correlation_sums.c.correlation_id,
            ).where(correlation_sums.c.total != ZERO),
        ).subquery("discrepancies")
        counts = select(
            select(func.count(Wallet.id)).scalar_subquery().label("wallets_checked"),
            select(func.count())
            .select_from(correlation_sums)
            .scalar_subquery()
            .label("correlations_checked"),
        ).subquery("counts")
        rows = await self._session.execute(
            select(counts, discrepancies)
            .select_from(counts.outerjoin(discrepancies, true()))
            .order_by(discrepancies.c.wallet_id.asc().nulls_last(), discrepancies.c.correlation_id)
        )
        snapshot = ReconciliationSnapshot()
        for wallet_count, correlation_count, wallet_id, balance, total, correlation_id in rows:
            snapshot.wallets_checked = wallet_count
            snapshot.correlations_checked = correlation_count
            if wallet_id is not None:
                snapshot.wallet_drifts.append((wallet_id, balance, total))
            elif correlation_id is not None:
                snapshot.correlation_drifts.append((correlation_id, total))
        return snapshot
