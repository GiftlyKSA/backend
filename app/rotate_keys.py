"""CLI to rotate field-encryption keys.

Run after adding a new key version to ``FIELD_ENCRYPTION_KEYS`` and bumping
``FIELD_ENCRYPTION_KEY_VERSION`` to re-encrypt courier identity, conversation previews,
and withdrawal IBANs. Retain key versions used by courier payout IBANs, message bodies
and stored operation results while their ciphertext exists.

    uv run python -m app.rotate_keys
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.services.key_rotation_service import KeyRotationService


async def _run() -> None:
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            report = await KeyRotationService(session=session, settings=settings).rotate()
    finally:
        await engine.dispose()
    print(
        "Key rotation complete: "
        f"national_id={report.courier_national_id}, "
        f"passport_id={report.courier_passport_id}, "
        f"preview={report.conversation_preview}, "
        f"iban={report.withdrawal_iban} (total {report.total})."
    )
    print(
        f"Fields retaining write-time keys: {', '.join(report.retained_ciphertext_fields)}. "
        "Keep referenced key versions in FIELD_ENCRYPTION_KEYS."
    )


def main() -> None:
    """Run the key-rotation job."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
