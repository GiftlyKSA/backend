"""Read owned wallet statements without deriving totals from a page."""

from datetime import date
from uuid import UUID

from app.core.exceptions import NotFoundError, ValidationDomainError
from app.core.money import money_str
from app.repositories.wallet_repository import WalletRepository
from app.schemas.wallet_statement import StatementTotals, WalletStatement
from app.schemas.wallets import TransactionResponse
from app.services.courier_eligibility_service import CourierEligibilityService
from app.services.reporting_dates import reporting_bounds


class WalletStatementService:
    """Use current eligibility and bounded SQL aggregation for financial history."""

    def __init__(self, wallets: WalletRepository, eligibility: CourierEligibilityService) -> None:
        """Wire owned-wallet persistence and the shared eligibility boundary."""
        self._wallets = wallets
        self._eligibility = eligibility

    async def read(
        self,
        actor_id: UUID,
        *,
        from_date: date,
        to_date: date,
        limit: int,
        cursor: UUID | None,
    ) -> WalletStatement:
        """Return current whole-range totals with a newest-first transaction page."""
        if from_date > to_date or (to_date - from_date).days >= 366:
            raise ValidationDomainError("Statement range must be between 1 and 366 days.")
        await self._eligibility.require_marketplace_actor(actor_id)
        wallet = await self._wallets.get_by_user(actor_id)
        if wallet is None:
            raise NotFoundError("Wallet not found.")
        start, end = reporting_bounds(from_date, to_date)
        assert start is not None and end is not None
        result = await self._wallets.statement(
            wallet.id,
            start=start,
            end=end,
            limit=limit + 1,
            cursor=cursor,
        )
        items = [
            TransactionResponse(
                id=str(row.id),
                amount=money_str(row.amount),
                type=str(row.type),
                status=str(row.status),
                balance_after=money_str(row.balance_after),
                created_at=row.created_at.isoformat(),
                description=row.description,
                order_id=str(row.reference_order_id) if row.reference_order_id else None,
                invoice_id=str(row.reference_invoice_id) if row.reference_invoice_id else None,
                payment_intent_id=str(row.reference_intent_id) if row.reference_intent_id else None,
            )
            for row in result.items[:limit]
        ]
        totals = StatementTotals(
            **{name: money_str(amount) for name, amount in result.totals.items()},
            settled_net=money_str(
                result.totals["settled_credits"] - result.totals["settled_debits"]
            ),
        )
        return WalletStatement(
            currency=wallet.currency,
            from_date=from_date,
            to_date=to_date,
            as_of=result.as_of,
            totals=totals,
            items=items,
            next_cursor=str(items[-1].id) if len(result.items) > limit else None,
        )
