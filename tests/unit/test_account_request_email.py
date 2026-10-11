"""Account closure emails render the detailed notice rather than a paid invoice."""

import httpx
import pytest
from app.integrations.email.sndr_client import SndrEmailClient


@pytest.mark.asyncio
async def test_sndr_account_request_renders_notice_with_stable_idempotency() -> None:
    seen = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "fake"})

    client = SndrEmailClient("https://api.sndr.sh", "fake-key", "support@example.test", "Giftly")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        await client.send_transactional(
            "customer@example.test",
            "account-request",
            {
                "request_id": "owned-request",
                "body": "Restore within 14 days; then administrative review.",
            },
        )
    finally:
        await client.aclose()
    assert seen[0].headers["Idempotency-Key"] == "giftly-account-request-owned-request"
    assert b"Restore within 14 days" in seen[0].content
    assert b"paid invoice" not in seen[0].content
