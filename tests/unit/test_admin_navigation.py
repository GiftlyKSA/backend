"""Admin pages expose the shared CRUD editor for their underlying tables."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from jinja2 import Environment, FileSystemLoader

_TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "admin" / "templates"


@pytest.mark.parametrize(
    "section,table",
    [
        ("users", "users"),
        ("couriers", "courier_profiles"),
        ("orders", "orders"),
        ("invoices", "invoices"),
        ("promos", "promos"),
        ("disputes", "disputes"),
        ("withdrawals", "withdrawals"),
        ("wallets", "wallets"),
        ("topups", "wallet_topups"),
    ],
)
def test_section_has_table_crud_links(section: str, table: str) -> None:
    environment = Environment(loader=FileSystemLoader(_TEMPLATES), autoescape=True)
    record_id = uuid4()
    request = SimpleNamespace(url=SimpleNamespace(path=f"/v1/admin/admin/{section}/{record_id}"))
    ctx = SimpleNamespace(csrf_token="test")

    html = environment.get_template("base.html").render(
        request=request, ctx=ctx, lang="en", theme="light", tr=str
    )

    assert f'/v1/admin/admin/tables/{table}"' in html
    assert f'/v1/admin/admin/tables/{table}/new"' in html
    assert f'/v1/admin/admin/tables/{table}/{record_id}/edit"' in html
    assert f">Add {table.replace('_', ' ').title()}</a>" in html
    assert "Edit or delete this record" in html


@pytest.mark.parametrize("section,collection", [("invoices", "invoices"), ("promos", "promos")])
def test_invoice_and_promo_pages_offer_crud(section: str, collection: str) -> None:
    environment = Environment(loader=FileSystemLoader(_TEMPLATES), autoescape=True)
    record_id = uuid4()
    request = SimpleNamespace(url=SimpleNamespace(path=f"/v1/admin/admin/{section}"))
    ctx = SimpleNamespace(csrf_token="test")
    row = SimpleNamespace(
        id=record_id,
        status="DRAFT",
        total_amount="1.00",
        created_at="now",
        code="TEST",
        discount_type="FIXED",
        is_active=True,
        used_count=0,
        max_total_usages=None,
    )

    html = environment.get_template(f"{section}.html").render(
        request=request, ctx=ctx, lang="en", theme="light", tr=str, **{collection: [row]}
    )

    assert f'/v1/admin/admin/tables/{section}/new"' in html
    assert f'/v1/admin/admin/tables/{section}/{record_id}/edit"' in html
    assert "Read-only" not in html


def test_header_uses_preference_buttons_and_account_menu() -> None:
    environment = Environment(loader=FileSystemLoader(_TEMPLATES), autoescape=True)
    request = SimpleNamespace(url=SimpleNamespace(path="/v1/admin/admin/orders"))
    ctx = SimpleNamespace(csrf_token="test")

    html = environment.get_template("base.html").render(
        request=request, ctx=ctx, lang="ar", theme="light", tr=str
    )

    header = html.split('<header class="workspace-header">', 1)[1].split("</header>", 1)[0]
    assert "View all" not in header
    assert "Add record" not in header
    assert '<button type="submit"' in header
    assert 'name="theme" value="dark"' in header
    assert 'name="lang" value="en"' in header
    assert '<details class="account-menu">' in header
    assert 'action="/v1/admin/admin/logout"' in header
