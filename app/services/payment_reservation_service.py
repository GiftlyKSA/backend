"""Release intent-owned wallet reservations inside the caller's transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.money import ZERO
from app.models import PaymentIntent
from app.models.enums import PaymentIntentStatus, PaymentPurpose
from app.repositories.payment_repository import PaymentRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.money_service import MoneyService


class PaymentReservationService:
    """Shared reservation rules for payment failures, cancellation and expiry."""

    def __init__(
        self, *, payments: PaymentRepository, wallets: WalletRepository, money: MoneyService
    ) -> None:
        """Use repositories sharing the caller's transaction."""
        self._payments = payments
        self._wallets = wallets
        self._money = money

    async def release_locked_intent(self, intent: PaymentIntent) -> None:
        """Release a locked NEW attempt; the caller must transition it atomically."""
        if (
            intent.status is not PaymentIntentStatus.NEW
            or intent.purpose is not PaymentPurpose.ORDER_INVOICE
            or intent.wallet_reserved_amount <= ZERO
        ):
            return
        wallet = await self._wallets.get_by_user(intent.user_id)
        if wallet is None:
            raise NotFoundError("Wallet not found.")
        await self._money.release_hold(wallet_id=wallet.id, amount=intent.wallet_reserved_amount)

    async def expire_for_invoice(self, invoice_id: UUID) -> None:
        """Release and expire the active attempt; the caller must hold the invoice lock."""
        intent = await self._payments.get_open_intent_for_invoice(invoice_id, for_update=True)
        if intent is None:
            return
        await self.release_locked_intent(intent)
        await self._payments.mark_expired(intent)


def build_payment_reservation_service(session: AsyncSession) -> PaymentReservationService:
    """Bind reservation collaborators to one session."""
    wallets = WalletRepository(session)
    return PaymentReservationService(
        payments=PaymentRepository(session), wallets=wallets, money=MoneyService(wallets)
    )
