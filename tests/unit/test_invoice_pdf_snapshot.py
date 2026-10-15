"""Invoice rendering does not retain DB transactions or mutable ORM state."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.deps import Actor
from app.core.exceptions import NotFoundError
from app.models.enums import InvoiceStatus, UserRole
from app.routers.invoices import download_invoice_pdf

from tests.unit.test_invoice_delivery_changes import invoice, items


@pytest.mark.parametrize("cached", [True, False])
async def test_pdf_reads_release_before_render_and_use_independent_snapshot(monkeypatch, cached):
    stored, lines = invoice(), items()
    original_title = lines[0].title
    db = AsyncMock()
    service = SimpleNamespace(get_invoice_for_actor=AsyncMock(return_value=(stored, lines)))

    async def release():
        stored.status = InvoiceStatus.PAID
        lines[0].title = "Changed after snapshot"

    db.commit.side_effect = release

    def check(snapshot, line_snapshots):
        db.commit.assert_awaited_once()
        assert snapshot is not stored
        assert snapshot.status is InvoiceStatus.ISSUED
        assert isinstance(line_snapshots, tuple)
        assert line_snapshots[0].title == original_title
        with pytest.raises(FrozenInstanceError):
            snapshot.currency = "USD"
        with pytest.raises(FrozenInstanceError):
            line_snapshots[0].title = "Mutation"
        return b"%PDF-1.4 snapshot"

    async def from_cache(snapshot, line_snapshots):
        return check(snapshot, line_snapshots)

    cache = SimpleNamespace(render=from_cache) if cached else None
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(invoice_pdf_cache=cache)))
    monkeypatch.setattr("app.routers.invoices._service", lambda *_: service)
    monkeypatch.setattr("app.routers.invoices.render_invoice_pdf", check)
    response = await download_invoice_pdf(
        request, db, stored.id, Actor(uuid4(), UserRole.CUSTOMER, "test-session")
    )
    assert response.body == b"%PDF-1.4 snapshot"
    assert response.headers["cache-control"] == "private, no-store"


async def test_pdf_authorization_failure_never_looks_up_cache(monkeypatch):
    cache, db = SimpleNamespace(render=AsyncMock()), AsyncMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(invoice_pdf_cache=cache)))
    service = SimpleNamespace(
        get_invoice_for_actor=AsyncMock(side_effect=NotFoundError("Invoice not found."))
    )
    monkeypatch.setattr("app.routers.invoices._service", lambda *_: service)
    with pytest.raises(NotFoundError):
        await download_invoice_pdf(
            request, db, uuid4(), Actor(uuid4(), UserRole.CUSTOMER, "test-session")
        )
    cache.render.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_pdf_failed_read_commit_never_renders(monkeypatch):
    cache, db = SimpleNamespace(render=AsyncMock()), AsyncMock()
    stored, lines = invoice(), items()
    db.commit.side_effect = RuntimeError("Synthetic transaction failure")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(invoice_pdf_cache=cache)))
    service = SimpleNamespace(get_invoice_for_actor=AsyncMock(return_value=(stored, lines)))
    monkeypatch.setattr("app.routers.invoices._service", lambda *_: service)
    with pytest.raises(RuntimeError, match="Synthetic transaction failure"):
        await download_invoice_pdf(
            request, db, stored.id, Actor(uuid4(), UserRole.CUSTOMER, "test-session")
        )
    cache.render.assert_not_awaited()


def test_snapshot_preserves_pdf_drawing_content_and_cache_fingerprint():
    import base64
    import re
    import zlib

    from app.services.invoice_pdf import render_invoice_pdf, snapshot_invoice_pdf
    from app.services.invoice_pdf_cache import _fingerprint

    stored, lines = invoice(), items()
    frozen_header, frozen_lines = snapshot_invoice_pdf(stored, lines)
    assert _fingerprint(stored, lines) == _fingerprint(frozen_header, frozen_lines)

    def streams(document):
        return [
            zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
            for stream in re.findall(rb"stream\r?\n(.*?)endstream", document, re.DOTALL)
        ]

    assert streams(render_invoice_pdf(stored, lines)) == streams(
        render_invoice_pdf(frozen_header, frozen_lines)
    )
