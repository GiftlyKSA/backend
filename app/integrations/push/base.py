"""The push-notification client contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PushClient(ABC):
    """Sends push notifications to a user's devices."""

    @abstractmethod
    async def send_push(
        self, tokens: list[str], title: str, body: str, *, data: dict[str, str] | None = None
    ) -> None:
        """Send a push to ``tokens``.

        Note:
            The body must never contain Restricted data (e.g. chat message text).
        """
