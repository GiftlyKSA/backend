"""Expo transport with durable ticket storage and fenced device cleanup."""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.integrations.push.base import PushClient

_URL = "https://exp.host/--/api/v2/push"


class ExpoSendError(RuntimeError):
    """A safe provider outcome with a bounded requested cooldown."""

    def __init__(
        self, code: str, *, retry_after: float = 0, message: str = "Expo send failed."
    ) -> None:
        """Retain no provider response bodies or credentials."""
        super().__init__(message)
        self.code = code
        self.retry_after = min(max(retry_after, 0), 86400) if math.isfinite(retry_after) else 0


@dataclass(frozen=True)
class DeviceSnapshot:
    """Identify the exact device registration present before sending."""

    device_id: uuid.UUID
    token: str
    last_seen_at: datetime


class ExpoReceiptStore(Protocol):
    """Persist transport outcomes without putting database access in the adapter."""

    async def snapshot_devices(
        self, tokens: list[str], *, data: dict[str, str] | None = None
    ) -> dict[str, DeviceSnapshot]:
        """Capture current registrations before sending."""
        ...

    async def save_tickets(self, tickets: list[tuple[str, DeviceSnapshot]]) -> None:
        """Commit accepted tickets before sending another chunk."""
        ...

    async def remove_invalid_batch(self, snapshots: list[DeviceSnapshot]) -> None:
        """Remove an unchanged invalid registration."""
        ...

    async def aclose(self) -> None:
        """Release owned resources."""
        ...


class ExpoOutcome(BaseModel):
    """Validate the provider outcome without retaining its personal error message."""

    status: Literal["ok", "error"]
    id: str | None = Field(default=None, max_length=256)
    details: dict[str, str] = Field(default_factory=dict)


class _Tickets(BaseModel):
    data: list[ExpoOutcome]


class _Receipts(BaseModel):
    data: dict[str, ExpoOutcome]


class ExpoPushClient(PushClient):
    """Send bounded chunks through one pooled Expo HTTP client."""

    def __init__(
        self,
        receipt_store: ExpoReceiptStore | None = None,
        access_token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Inject durable ticket handling and optional enhanced-security credentials."""
        self._store = receipt_store
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers=headers,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )

    async def aclose(self) -> None:
        """Close the HTTP pool and any resources owned by the receipt store."""
        await self._client.aclose()
        if self._store is not None:
            await self._store.aclose()

    async def _request_once(self, endpoint: str, payload: object) -> httpx.Response:
        async with self._client.stream("POST", f"{_URL}/{endpoint}", json=payload) as streamed:
            content = bytearray()
            if streamed.is_success:
                async for chunk in streamed.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > 1024 * 1024:
                        raise ExpoSendError(
                            "ProviderUnavailable", message="Expo response exceeds its size limit."
                        )
                    content.extend(chunk)
            return httpx.Response(
                streamed.status_code,
                content=bytes(content),
                headers={
                    name: value
                    for name, value in streamed.headers.items()
                    if name.lower() not in {"content-encoding", "content-length"}
                },
                request=streamed.request,
            )

    async def _post(self, endpoint: str, payload: object) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._request_once(endpoint, payload)
            except httpx.TransportError:
                if attempt == 2:
                    raise RuntimeError("Expo transport unavailable.") from None
            else:
                if response.is_success:
                    return response
                if response.status_code != 429 and response.status_code < 500:
                    raise RuntimeError("Expo request rejected.")
                if attempt == 2:
                    raise RuntimeError("Expo transport unavailable.")
            await asyncio.sleep(0.25 * 2**attempt)
        raise RuntimeError("Expo transport unavailable.")

    async def _handle_tickets(
        self, tokens: list[str], snapshots: dict[str, DeviceSnapshot], tickets: list[ExpoOutcome]
    ) -> None:
        if len(tickets) != len(tokens):
            raise RuntimeError("Invalid Expo ticket count.")
        if self._store is None:
            raise RuntimeError("Direct push delivery requires durable receipt storage.")
        accepted = []
        invalid = []
        failed = False
        for token, ticket in zip(tokens, tickets, strict=True):
            snapshot = snapshots.get(token)
            if snapshot is None:
                continue
            if ticket.status == "ok" and ticket.id:
                accepted.append((ticket.id, snapshot))
            elif ticket.details.get("error") == "DeviceNotRegistered":
                invalid.append(snapshot)
            else:
                failed = True
        if accepted:
            await self._store.save_tickets(accepted)
        if invalid:
            await self._store.remove_invalid_batch(invalid)
        if failed:
            raise RuntimeError("Expo ticket rejected.")

    async def send_push(
        self, tokens: list[str], title: str, body: str, *, data: dict[str, str] | None = None
    ) -> None:
        """Preserve safe caller hints and save each accepted chunk durably."""
        if self._store is None:
            raise RuntimeError("Direct push delivery requires durable receipt storage.")
        for offset in range(0, len(tokens), 100):
            chunk = list(dict.fromkeys(tokens[offset : offset + 100]))
            snapshots = await self._store.snapshot_devices(chunk, data=data)
            chunk = [token for token in chunk if token in snapshots]
            if not chunk:
                continue
            response = await self._post(
                "send",
                [
                    {
                        "to": token,
                        "title": title,
                        "body": body,
                        "sound": "default",
                        **({"data": data} if data else {}),
                    }
                    for token in chunk
                ],
            )
            try:
                tickets = _Tickets.model_validate_json(response.content).data
            except ValidationError:
                raise RuntimeError("Invalid Expo tickets.") from None
            await self._handle_tickets(chunk, snapshots, tickets)

    async def get_receipts(self, ticket_ids: list[str]) -> dict[str, ExpoOutcome]:
        """Read one bounded receipt batch; absence means it is not available yet."""
        if not ticket_ids or len(ticket_ids) > 1000:
            raise ValueError("Expo receipt batches require 1 to 1000 IDs.")
        response = await self._checked_request("getReceipts", {"ids": ticket_ids})
        try:
            return _Receipts.model_validate_json(response.content).data
        except ValidationError:
            raise RuntimeError("Invalid Expo receipts.") from None

    async def send_messages(
        self, messages: list[dict[str, str | dict[str, str]]]
    ) -> list[ExpoOutcome]:
        """Perform one transport attempt; the database owns send retry policy."""
        if not messages or len(messages) > 100:
            raise ValueError("Expo send batches require 1 to 100 messages.")
        response = await self._checked_request("send", messages)
        try:
            outcomes = _Tickets.model_validate_json(response.content).data
        except ValidationError:
            raise ExpoSendError("ProviderUnavailable") from None
        if len(outcomes) != len(messages):
            raise ExpoSendError("ProviderUnavailable")
        return outcomes

    async def _checked_request(self, endpoint: str, payload: object) -> httpx.Response:
        try:
            response = await self._request_once(endpoint, payload)
        except httpx.TransportError:
            raise ExpoSendError("ProviderUnavailable") from None
        if not response.is_success:
            from datetime import UTC
            from email.utils import parsedate_to_datetime

            header = response.headers.get("Retry-After", "0")
            try:
                cooldown = float(header)
            except ValueError:
                try:
                    cooldown = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
                except TypeError, ValueError, OverflowError:
                    cooldown = 0
            code = (
                "ProviderUnavailable"
                if response.status_code == 429 or response.status_code >= 500
                else "InvalidCredentials"
                if response.status_code in {401, 403}
                else "ProviderError"
            )
            raise ExpoSendError(code, retry_after=cooldown)
        return response
