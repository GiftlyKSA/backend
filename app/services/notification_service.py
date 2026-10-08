"""Push notifications.

Notifications are BEST-EFFORT: a push failure is logged and swallowed so it never breaks
the flow that triggered it. The body must NEVER carry Restricted data — chat text, exact
identity numbers — only a neutral prompt that draws the user into the app.
"""

from __future__ import annotations

import logging
import uuid

from app.integrations.push.base import PushClient
from app.repositories.device_token_repository import DeviceTokenRepository

_logger = logging.getLogger("app.services.notification")
_PUSH_BATCH_SIZE = 500


class NotificationService:
    """Sends best-effort push notifications to users and to city couriers."""

    def __init__(self, *, devices: DeviceTokenRepository, push: PushClient) -> None:
        """Wire the device-token repository and the push client."""
        self._devices = devices
        self._push = push

    async def notify_user(self, *, user_id: uuid.UUID, title: str, body: str) -> int:
        """Push to all of a user's devices. Returns how many tokens were targeted."""
        return await self._send_pages(user_id=user_id, title=title, body=body)

    async def notify_city_couriers(self, *, city: str, title: str, body: str) -> int:
        """Push to every active, verified courier in a city (the new-order radar ping)."""
        return await self._send_pages(city=city, title=title, body=body)

    async def _send_pages(
        self,
        *,
        title: str,
        body: str,
        user_id: uuid.UUID | None = None,
        city: str | None = None,
    ) -> int:
        targeted = 0
        after = None
        while True:
            page = await self._devices.token_page(
                user_id=user_id,
                city=city,
                after=after,
                limit=_PUSH_BATCH_SIZE,
            )
            if not page:
                return targeted
            await self._send([token for _, token in page], title, body)
            targeted += len(page)
            after = page[-1][0]
            if len(page) < _PUSH_BATCH_SIZE:
                return targeted

    async def _send(self, tokens: list[str], title: str, body: str) -> None:
        if not tokens:
            return
        for start in range(0, len(tokens), _PUSH_BATCH_SIZE):
            batch = tokens[start : start + _PUSH_BATCH_SIZE]
            try:
                await self._push.send_push(batch, title, body)
            except Exception:  # noqa: BLE001 - a push failure must never break the caller
                _logger.exception("push notification failed for %d token(s)", len(batch))
