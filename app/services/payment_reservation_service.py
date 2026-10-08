"""Release intent-owned wallet reservations inside the caller's transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PaymentSessionPendingError
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
        if intent.status is not PaymentIntentStatus.NEW:
            return
        if intent.purpose is PaymentPurpose.ORDER_INVOICE and intent.wallet_reserved_amount > ZERO:
            wallet = await self._wallets.get_by_user(intent.user_id)
            if wallet is None:
                raise NotFoundError("Wallet not found.")
            await self._money.release_hold(
                wallet_id=wallet.id, amount=intent.wallet_reserved_amount
            )
        await self._money.reverse_pending_intent(intent.id)

    async def expire_for_invoice(self, invoice_id: UUID) -> None:
        """Release and expire the active attempt; the caller must hold the invoice lock."""
        intent = await self._payments.get_open_intent_for_invoice(invoice_id, for_update=True)
        if intent is None:
            return
        if intent.checkout_provider != "SIMULATED":
            raise PaymentSessionPendingError(
                "Close or reconcile the payment session before changing this invoice."
            )
        await self.release_locked_intent(intent)
        await self._payments.mark_expired(intent)


def build_payment_reservation_service(session: AsyncSession) -> PaymentReservationService:
    """Bind reservation collaborators to one session."""
    wallets = WalletRepository(session)
    return PaymentReservationService(
        payments=PaymentRepository(session), wallets=wallets, money=MoneyService(wallets)
    )
