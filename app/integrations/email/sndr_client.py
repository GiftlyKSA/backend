"""sndr.sh send contract, verified against official API reference on 2026-10-03."""

from __future__ import annotations

import httpx

from app.integrations.email.base import EmailClient


class SndrEmailClient(EmailClient):
    """Sends transactional email through sndr.sh over HTTPS via httpx."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        from_email: str,
        from_name: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Hold the vendor configuration and one pooled HTTP client (audit PERF-1)."""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._from_email = from_email
        self._from_name = from_name
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        """Close the pooled HTTP client (wired to app shutdown)."""
        await self._client.aclose()

    async def send_transactional(
        self, to_email: str, template_key: str, variables: dict[str, object]
    ) -> None:
        """Send a paid invoice with safe plain text and a private PDF attachment."""
        invoice_id = str(variables.get("invoice_id", ""))
        payload: dict[str, object] = {
            "from": f"{self._from_name} <{self._from_email}>",
            "to": [to_email],
            "subject": "Your Giftly paid invoice",
            "text": (
                f"Thank you for your payment. Invoice: {invoice_id}\n"
                f"Total: {variables.get('currency', 'SAR')} {variables.get('total_amount', '')}\n"
                "Your invoice PDF is attached."
            ),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if invoice_id:
            headers["Idempotency-Key"] = f"giftly-invoice-receipt-{invoice_id}"
        attachment = variables.get("pdf_base64")
        if attachment:
            payload["attachments"] = [
                {
                    "filename": "giftly-invoice.pdf",
                    "content": attachment,
                    "content_type": "application/pdf",
                }
            ]
        response = await self._client.post(
            f"{self._base_url}/v1/send", json=payload, headers=headers
        )
        response.raise_for_status()
