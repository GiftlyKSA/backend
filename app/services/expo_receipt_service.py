"""Durable Expo receipt recovery without database transactions across network calls."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.integrations.push.expo import DeviceSnapshot, ExpoOutcome, ExpoPushClient
from app.repositories.expo_receipt_repository import ExpoReceiptRepository, ReceiptClaim

_logger = logging.getLogger(__name__)
_RETRYABLE = {"MessageRateExceeded", "ExpoServerError", "ProviderUnavailable"}


class ExpoReceiptService:
    """Commit tickets and device fences in independent short transactions."""

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], *, owned_engine: AsyncEngine | None = None
    ) -> None:
        """Use a caller-owned session factory or dispose an explicitly owned pool."""
        self._factory = factory
        self._owned_engine = owned_engine

    async def aclose(self) -> None:
        """Dispose the pool only when constructed for this integration."""
        if self._owned_engine is not None:
            await self._owned_engine.dispose()

    async def snapshot_devices(
        self, tokens: list[str], *, data: dict[str, str] | None = None
    ) -> dict[str, DeviceSnapshot]:
        """Capture current registrations before the network request."""
        order_id = None
        if data is not None and data.get("type") == "ORDER_AVAILABLE":
            try:
                order_id = uuid.UUID(data.get("order_id", ""))
            except ValueError:
                raise ValueError("Order push requires a valid order UUID.") from None
        async with self._factory() as session:
            return await ExpoReceiptRepository(session).snapshot_devices(tokens, order_id=order_id)

    async def save_tickets(self, tickets: list[tuple[str, DeviceSnapshot]]) -> None:
        """Durably commit every accepted chunk before sending later chunks."""
        async with self._factory() as session:
            await ExpoReceiptRepository(session).save_tickets(tickets, now=datetime.now(UTC))
            await session.commit()

    async def remove_invalid_batch(self, snapshots: list[DeviceSnapshot]) -> None:
        """Delete an invalid token only while its original registration survives."""
        async with self._factory() as session:
            await ExpoReceiptRepository(session).remove_invalid_batch(snapshots)
            await session.commit()

    def _resolution(
        self,
        claim: ReceiptClaim,
        outcome: ExpoOutcome | None,
    ) -> tuple[ReceiptClaim, str | None, bool, bool]:
        code = outcome.details.get("error") if outcome is not None else None
        retry = outcome is None or (outcome.status == "error" and code in _RETRYABLE)
        safe_code = (
            code
            if code
            in _RETRYABLE
            | {"DeviceNotRegistered", "MessageTooBig", "MismatchSenderId", "InvalidCredentials"}
            else "ProviderError"
        )
        if outcome is not None and outcome.status == "error":
            _logger.warning("Expo receipt delivery failed (%s)", safe_code)
        return (
            claim,
            None if outcome is not None and outcome.status == "ok" else safe_code,
            retry,
            code == "DeviceNotRegistered",
        )

    async def drain(self, client: ExpoPushClient, *, batches: int = 5) -> int:
        """Poll bounded ready batches with finite durable retry and lease fencing."""
        completed = 0
        for _ in range(min(batches, 5)):
            async with self._factory() as session:
                claims = await ExpoReceiptRepository(session).claim_pending(now=datetime.now(UTC))
                await session.commit()
            if not claims:
                break
            try:
                outcomes = await client.get_receipts([claim.ticket_id for claim in claims])
            except RuntimeError:
                _logger.warning("Expo receipt lookup unavailable.")
                outcomes = {}
            for claim in claims:
                outcome = outcomes.get(claim.ticket_id)
                completed += outcome is not None
            async with self._factory() as session:
                await ExpoReceiptRepository(session).finish_batch(
                    [self._resolution(claim, outcomes.get(claim.ticket_id)) for claim in claims],
                    now=datetime.now(UTC),
                )
                await session.commit()
        return completed
