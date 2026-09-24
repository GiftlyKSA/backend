"""Financial expiry policy; callers own per-item commit and rollback boundaries."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import ZERO
from app.models.enums import InvoiceStatus, OrderStatus, PaymentIntentStatus, PaymentPurpose
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.promo_repository import PromoRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.money_service import MoneyService
from app.services.order_state import assert_transition
from app.services.payment_reservation_service import PaymentReservationService
from app.services.promo_service import PromoService


class ExpiryService:
    """Expire locked invoice payments and wallet top-ups without moving ledger funds."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind all expiry collaborators to the caller's transaction."""
        self._invoices = InvoiceRepository(session)
        self._orders = OrderRepository(session)
        self._payments = PaymentRepository(session)
        self._wallets = WalletRepository(session)
        self._money = MoneyService(self._wallets)
        self._reservations = PaymentReservationService(
            payments=self._payments, wallets=self._wallets, money=self._money
        )
        self._promos = PromoService(PromoRepository(session))

    async def expire_invoice(self, invoice_id: UUID) -> bool:
        """Release a lapsed issued invoice's reservations and reopen its order."""
        invoice = await self._invoices.lock(invoice_id)
        if (
            invoice is None
            or invoice.status is not InvoiceStatus.ISSUED
            or invoice.expires_at is None
            or invoice.expires_at >= datetime.now(UTC)
        ):
            return False
        await self._reservations.expire_for_invoice(invoice.id)
        await self._promos.release(invoice_id=invoice.id)
        invoice.status = InvoiceStatus.EXPIRED
        order = await self._orders.lock(invoice.order_id)
        if order is not None and order.status is OrderStatus.WAITING_PAYMENT:
            assert_transition(order.status, OrderStatus.ASSIGNED)
            order.status = OrderStatus.ASSIGNED
            order.total_amount = ZERO
        await self._invoices.flush()
        return True

    async def expire_topup_intent(self, intent_id: UUID) -> bool:
        """Expire only a still-NEW, lapsed top-up after acquiring its row lock."""
        intent = await self._payments.lock_intent(intent_id)
        if (
            intent is None
            or intent.purpose is not PaymentPurpose.WALLET_TOPUP
            or intent.status is not PaymentIntentStatus.NEW
            or intent.expires_at >= datetime.now(UTC)
        ):
            return False
        await self._payments.mark_expired(intent)
        return True
