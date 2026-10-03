"""Preview unpaid VAT repairs; --apply explicitly commits immutable revisions."""

import argparse
import asyncio
import json
import logging
from uuid import UUID

from app.core.audit_context import set_audit_actor
from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.core.exceptions import ConflictError
from app.repositories.invoice_repository import InvoiceRepository
from app.services.invoice_vat_repair_service import InvoiceVatRepairService


async def run(*, apply: bool, limit: int, after: UUID | None) -> None:
    """Report one bounded page; rollback previews and commit each repair separately."""
    engine = build_engine(get_settings())
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            ids = await InvoiceRepository(session).list_vat_repair_ids(after=after, limit=limit)
        for invoice_id in ids:
            async with factory() as session:
                try:
                    await set_audit_actor(session, category="SYSTEM", actor_user_id=None)
                    invoice, result = await InvoiceVatRepairService(session).repair(
                        invoice_id, apply=apply
                    )
                    report = {
                        "invoice_id": str(invoice_id),
                        "result_id": str(invoice.id),
                        "tax": str(result.tax_amount),
                        "total": str(result.total_amount),
                        "applied": apply and invoice.id != invoice_id,
                    }
                    if apply:
                        await session.commit()
                    else:
                        await session.rollback()
                    print(json.dumps(report))
                except ConflictError as exc:
                    await session.rollback()
                    print(json.dumps({"invoice_id": str(invoice_id), "skipped": str(exc)}))
                except Exception:
                    await session.rollback()
                    logging.getLogger(__name__).exception(
                        "Invoice VAT repair failed for %s", invoice_id
                    )
                    raise
        print(json.dumps({"next_after": str(ids[-1]) if len(ids) == limit else None}))
    finally:
        await engine.dispose()


def main() -> None:
    """Require an explicit apply flag; paid invoices are never selected."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=200, choices=range(1, 1001))
    parser.add_argument("--after", type=UUID)
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply, limit=args.limit, after=args.after))


if __name__ == "__main__":
    main()
