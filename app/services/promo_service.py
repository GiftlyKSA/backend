"""The promo engine — validation and atomic reserve/consume/release.

Codes are case-insensitive (normalized to upper). Validation reports a precise error
without reserving. Reservation uses the atomic conditional UPDATE so
a global usage cap ("first 20") can never be overshot by concurrent requests; the
per-user cap is enforced in the same transaction with the promo row already locked by
that UPDATE. Release returns a slot to the pool so an abandoned checkout never burns
one permanently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.core.exceptions import (
    PromoExpiredError,
    PromoInactiveError,
    PromoMinOrderNotMetError,
    PromoNotFoundError,
    PromoNotStartedError,
    PromoUsageExceededError,
    PromoUserLimitReachedError,
)
from app.core.pricing import PricingPromo, PromoDiscountKind, compute_promo_discount
from app.models import Promo
from app.models.enums import PromoDiscountType, PromoRedemptionStatus
from app.repositories.promo_repository import PromoRepository


@dataclass(frozen=True)
class PromoValidation:
    """A validated promo and the discount it would apply to a given base."""

    promo: Promo
    discount_amount: Decimal


def to_pricing_promo(promo: Promo) -> PricingPromo:
    """Adapt an ORM promo to the pure pricing engine's promo value object.

    Public so the invoice pipeline can run the full pricing engine with the same promo
    value object the promo engine validated, keeping the two in lock-step.
    """
    kind = (
        PromoDiscountKind.PERCENT
        if promo.discount_type is PromoDiscountType.PERCENT
        else PromoDiscountKind.FIXED
    )
    return PricingPromo(
        discount_type=kind,
        percent_value=promo.percent_value,
        fixed_amount=promo.fixed_amount,
        max_discount_amount=promo.max_discount_amount,
        min_order_amount=promo.min_order_amount,
    )


class PromoService:
    """Validates promos and manages their reservation lifecycle."""

    def __init__(self, promos: PromoRepository) -> None:
        """Bind the service to a promo repository (and its session)."""
        self._promos = promos

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def validate(
        self,
        *,
        code: str,
        discountable_base: Decimal,
        user_id: uuid.UUID,
        current_invoice_id: uuid.UUID | None = None,
    ) -> PromoValidation:
        """Validate a promo against a base amount without reserving it.

        Raises:
            PromoNotFoundError / PromoInactiveError / PromoNotStartedError /
            PromoExpiredError / PromoMinOrderNotMetError / PromoUsageExceededError /
            PromoUserLimitReachedError: The specific failure, each a 422 with its code.
        """
        promo = await self._promos.get_by_code(code)
        now = self._now()
        if promo is None:
            raise PromoNotFoundError
        if not promo.is_active:
            raise PromoInactiveError
        if promo.starts_at is not None and now < promo.starts_at:
            raise PromoNotStartedError
        if promo.ends_at is not None and now >= promo.ends_at:
            raise PromoExpiredError
        if discountable_base < promo.min_order_amount:
            raise PromoMinOrderNotMetError
        held = False
        if current_invoice_id is not None:
            redemption = await self._promos.get_redemption_by_invoice(current_invoice_id)
            held = (
                redemption is not None
                and redemption.promo_id == promo.id
                and redemption.user_id == user_id
                and redemption.status is PromoRedemptionStatus.RESERVED
            )
        if (
            promo.max_total_usages is not None
            and promo.used_count - int(held) >= promo.max_total_usages
        ):
            raise PromoUsageExceededError
        user_uses = await self._promos.count_user_redemptions(promo.id, user_id)
        if user_uses - int(held) >= promo.max_usages_per_user:
            raise PromoUserLimitReachedError
        discount = compute_promo_discount(discountable_base, to_pricing_promo(promo))
        return PromoValidation(promo=promo, discount_amount=discount)

    async def lock_replacement(self, code: str | None, previous_id: uuid.UUID | None) -> None:
        """Serialize promo edits and reserve/release in deterministic row order."""
        candidate = await self._promos.get_by_code(code) if code is not None else None
        if code is not None and candidate is None:
            raise PromoNotFoundError()
        identifiers = {
            identifier
            for identifier in (previous_id, candidate.id if candidate else None)
            if identifier is not None
        }
        await self._promos.lock_many(sorted(identifiers))

    async def reserve(
        self,
        *,
        promo: Promo,
        user_id: uuid.UUID,
        invoice_id: uuid.UUID,
        order_id: uuid.UUID,
        discount_amount: Decimal,
    ) -> None:
        """Reserve a promo usage for an invoice, inserting a RESERVED row.

        Must run inside the caller's transaction so a failed per-user check rolls back
        the atomic increment.

        Raises:
            PromoUsageExceededError: The global cap is exhausted (no slot claimed).
            PromoUserLimitReachedError: This user is over the per-user cap.
        """
        new_count = await self._promos.atomic_reserve(promo.id)
        if new_count is None:
            raise PromoUsageExceededError
        # The UPDATE row-locked the promo, so this count serializes against concurrent
        # reservations by the same user. Over the cap => raise => the whole tx rolls
        # back, undoing the increment above.
        user_uses = await self._promos.count_user_redemptions(promo.id, user_id)
        if user_uses >= promo.max_usages_per_user:
            raise PromoUserLimitReachedError
        await self._promos.insert_redemption(
            promo_id=promo.id,
            user_id=user_id,
            invoice_id=invoice_id,
            order_id=order_id,
            discount_amount=discount_amount,
        )

    async def consume(self, *, invoice_id: uuid.UUID) -> None:
        """Mark an invoice's reservation CONSUMED on payment."""
        redemption = await self._promos.get_redemption_by_invoice(invoice_id)
        if redemption is None or redemption.status is not PromoRedemptionStatus.RESERVED:
            return
        await self._promos.set_redemption_status(
            redemption, PromoRedemptionStatus.CONSUMED, self._now()
        )

    async def release(self, *, invoice_id: uuid.UUID) -> None:
        """Release an invoice's reservation, returning its slot to the pool.

        Idempotent: releasing an already-released or consumed reservation is a no-op.
        """
        redemption = await self._promos.get_redemption_by_invoice(invoice_id)
        if redemption is None or redemption.status is not PromoRedemptionStatus.RESERVED:
            return
        await self._promos.set_redemption_status(
            redemption, PromoRedemptionStatus.RELEASED, self._now()
        )
        await self._promos.atomic_release(redemption.promo_id)
