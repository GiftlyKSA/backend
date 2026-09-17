"""Fail closed until a verified Dhamen integration is available."""

from app.core.exceptions import PaymentsDisabledError
from app.integrations.payments.base import (
    PaymentCheckout,
    PaymentClient,
    PaymentCustomer,
    PaymentItem,
)


class DisabledPaymentClient(PaymentClient):
    """Never create a checkout or authenticate a callback in production."""

    async def create_payment_link(
        self,
        *,
        reference: str,
        customer: PaymentCustomer,
        items: tuple[PaymentItem, ...],
        success_redirect_url: str | None,
        failure_redirect_url: str | None,
    ) -> PaymentCheckout:
        """Reject checkout without making any network request."""
        raise PaymentsDisabledError()

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Reject every signature; there is no active production callback protocol."""
        return False
