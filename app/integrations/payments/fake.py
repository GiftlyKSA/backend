"""Deterministic simulated payment double for development and test environments."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import secrets

from app.core.config import Environment
from app.integrations._guard import forbid_in_production
from app.integrations.payments.base import (
    PaymentCheckout,
    PaymentClient,
    PaymentCustomer,
    PaymentItem,
)

_DEFAULT_WEBHOOK_KEY = hashlib.sha256(b"safe-gift-local-payment-key").hexdigest()


class FakePaymentClient(PaymentClient):
    """Return local hosted-checkout URLs and timestamped local test signatures."""

    def __init__(self, environment: Environment, webhook_secret: str | None = None) -> None:
        """Refuse construction in production and initialize a unique link sequence."""
        forbid_in_production(environment, type(self).__name__)
        self._counter = itertools.count(1)
        self._prefix = secrets.token_hex(4)
        self._webhook_secret = (webhook_secret or _DEFAULT_WEBHOOK_KEY).encode("utf-8")

    async def create_payment_link(
        self,
        *,
        reference: str,
        customer: PaymentCustomer,
        items: tuple[PaymentItem, ...],
        success_redirect_url: str | None,
        failure_redirect_url: str | None,
    ) -> PaymentCheckout:
        """Return a unique local URL; no live provider is contacted."""
        del reference, customer, items, success_redirect_url, failure_redirect_url
        payment_link_id = f"FAKE-PAYMENT-LINK-{self._prefix}-{next(self._counter):08d}"
        return PaymentCheckout(
            payment_link_id=payment_link_id,
            payment_url=(
                "http://localhost:8000/api/dev/simulation/simulate?"
                f"payment_link_id={payment_link_id}"
            ),
        )

    def sign(self, raw_body: bytes, *, timestamp: str = "1720000000") -> str:
        """Return the local test `t=...,v1=...` HMAC header."""
        message = timestamp.encode("utf-8") + b"." + raw_body
        signature = hmac.new(self._webhook_secret, message, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={signature}"

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify a timestamped signature in constant time."""
        values: dict[str, str] = {}
        for part in signature.split(","):
            key, separator, value = part.strip().partition("=")
            if separator and key and value:
                values[key] = value
        timestamp = values.get("t")
        provided = values.get("v1")
        if timestamp is None or provided is None:
            return False
        expected = self.sign(raw_body, timestamp=timestamp).split("v1=", maxsplit=1)[1]
        return hmac.compare_digest(expected, provided)
