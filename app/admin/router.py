"""Admin dashboard routes (SPEC SECTION 18.3).

Server-rendered Jinja pages mounted at ``/v1/admin/admin``. Every route calls the admin
services and never queries the DB directly. Reads are open to any authenticated
admin; every mutating action requires CSRF and writes an audit row.
Normal money-moving resolutions remain in the ledger service; raw table maintenance
does not run those transitions.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.assets import (
    DATETIME_SCRIPT,
    DATETIME_SCRIPT_VERSION,
    STYLESHEET,
    STYLESHEET_VERSION,
    THEME_SCRIPT,
    THEME_SCRIPT_VERSION,
)
from app.admin.deps import (
    SESSION_COOKIE,
    AdminContext,
    build_auth_service,
    client_ip,
    get_db,
    get_redis_from,
    get_settings_from,
    require_admin,
    verify_csrf,
)
from app.admin.i18n import LANGUAGE_COOKIE, LANGUAGES, THEME_COOKIE, THEMES, template_context
from app.admin.table_router import router as table_router
from app.core.admin_time import ADMIN_TIMEZONE, admin_input, finalize_admin_value
from app.core.exceptions import RateLimitedError, UnauthorizedError
from app.models.enums import UserRole
from app.repositories.admin_browse_query import BrowseOptions

router = APIRouter(prefix="/v1/admin/admin", tags=["admin"], include_in_schema=False)
router.include_router(table_router)

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_TEMPLATES.env.finalize = finalize_admin_value

DbDep = Annotated[AsyncSession, Depends(get_db)]
OptionalFilterUuid = Annotated[uuid.UUID | None, BeforeValidator(lambda value: value or None)]
OptionalFilterDatetime = Annotated[datetime | None, BeforeValidator(lambda value: value or None)]


def browse_options(
    page_size: int = Query(default=25, ge=25, le=100),
    sort_by: str = Query(default="", max_length=100),
    direction: Literal["asc", "desc"] = "desc",
    filter_field: str = Query(default="", max_length=100),
    filter_value: str = Query(default="", max_length=255),
    start_at: OptionalFilterDatetime = None,
    end_at: OptionalFilterDatetime = None,
    after: OptionalFilterUuid = None,
    before: OptionalFilterUuid = None,
    cursor_at: OptionalFilterDatetime = None,
) -> BrowseOptions:
    """Parse browser options; picker input uses Riyadh, cursors retain their offset."""
    start_at, end_at = admin_input(start_at), admin_input(end_at)
    cursor_at = (
        cursor_at.replace(tzinfo=UTC) if cursor_at and cursor_at.tzinfo is None else cursor_at
    )
    return BrowseOptions(
        page_size=page_size,
        sort_by=sort_by,
        direction=direction,
        filter_field=filter_field,
        filter_value=filter_value,
        start_at=start_at,
        end_at=end_at,
        after=after,
        before=before,
        cursor_at=cursor_at,
    )


BrowseDep = Annotated[BrowseOptions, Depends(browse_options)]


def _render(
    request: Request, template: str, *, status_code: int = 200, **context: object
) -> HTMLResponse:
    """Render a template with the request bound."""
    return _TEMPLATES.TemplateResponse(
        request,
        template,
        {"request": request, **template_context(request), **context},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/assets/admin.{version}.css")
async def dashboard_stylesheet(version: str) -> Response:
    """Serve only the exact stylesheet version referenced by this build's HTML."""
    if version != STYLESHEET_VERSION:
        raise HTTPException(status_code=404, detail="Stylesheet version not found")
    return Response(
        STYLESHEET,
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


async def _ctx(request: Request, db: AsyncSession) -> AdminContext:
    return await require_admin(request, db)


@router.get("/assets/theme.{version}.js")
async def dashboard_theme_script(version: str) -> Response:
    """Serve the theme switcher associated with this application build."""
    if version != THEME_SCRIPT_VERSION:
        raise HTTPException(status_code=404, detail="Theme script version not found")
    return Response(
        THEME_SCRIPT,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/assets/datetime.{version}.js")
async def dashboard_datetime_script(version: str) -> Response:
    """Serve timezone conversion code for this build without stale cached assets."""
    if version != DATETIME_SCRIPT_VERSION:
        raise HTTPException(status_code=404, detail="Datetime script version not found")
    return Response(
        DATETIME_SCRIPT,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/relationships/{table_name}/{field}")
async def relationship_choices(
    request: Request,
    db: DbDep,
    table_name: str,
    field: str,
    search: str = Query(default="", max_length=100),
    after: uuid.UUID | None = None,
    record_id: uuid.UUID | None = None,
) -> JSONResponse:
    """Load one page of related records without exposing credentials or private documents."""
    ctx = await _ctx(request, db)
    choices = await ctx.service.relationship_choices(
        table_name, field, record_id=record_id, search=search, after=after
    )
    return JSONResponse(choices, headers={"Cache-Control": "no-store"})


# --- Authentication ----------------------------------------------------------


@router.get("/preferences", include_in_schema=False)
async def preferences(
    request: Request,
    lang: str | None = None,
    theme: str | None = None,
    next: str = "/v1/admin/admin",
) -> RedirectResponse:
    """Persist display preferences without changing authentication state."""
    if lang is not None and lang not in LANGUAGES:
        raise HTTPException(status_code=400, detail="Unsupported language.")
    if theme is not None and theme not in THEMES:
        raise HTTPException(status_code=400, detail="Unsupported theme.")
    if (
        not next.startswith("/v1/admin/admin")
        or next.startswith("//")
        or any(char in next for char in ("\\", "\r", "\n"))
    ):
        next = "/v1/admin/admin"
    response = RedirectResponse(next, status_code=303, headers={"Cache-Control": "no-store"})
    secure = get_settings_from(request).is_production
    if lang is not None:
        response.set_cookie(
            LANGUAGE_COOKIE,
            lang,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/v1/admin/admin",
            max_age=31_536_000,
        )
    if theme is not None:
        response.set_cookie(
            THEME_COOKIE,
            theme,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/v1/admin/admin",
            max_age=31_536_000,
        )
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Show the admin login form."""
    return _render(request, "login.html", require_totp=get_settings_from(request).is_production)


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    db: DbDep,
    username: Annotated[str, Form(min_length=1, max_length=128)],
    password: Annotated[str, Form(min_length=1, max_length=1024)],
    totp_code: Annotated[str, Form(max_length=6)] = "",
) -> Response:
    """Verify environment credentials, create a session, and set its cookie."""
    settings = get_settings_from(request)
    auth = build_auth_service(db, get_redis_from(request), settings)
    try:
        result = await auth.complete_login(
            username=username,
            password=password,
            totp_code=totp_code,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:255] or None,
        )
    except (UnauthorizedError, RateLimitedError) as exc:
        return _render(
            request,
            "login.html",
            status_code=exc.status_code,
            username=username,
            error=exc.message,
            require_totp=settings.is_production,
        )
    request.state.audit_actor_id = result.admin.id
    request.state.audit_actor_category = "ADMIN"
    response = RedirectResponse("/v1/admin/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        result.raw_session_token,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path="/v1/admin/admin",
        max_age=settings.ADMIN_SESSION_TTL_MINUTES * 60,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request, db: DbDep, csrf_token: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """Revoke the current session and clear the cookie."""
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        ctx = await _ctx(request, db)
        verify_csrf(ctx, csrf_token, get_settings_from(request))
        await ctx.auth.logout(raw)
    response = RedirectResponse("/v1/admin/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/v1/admin/admin")
    return response


# --- Overview ----------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def overview(request: Request, db: DbDep) -> HTMLResponse:
    """Show the dashboard overview."""
    ctx = await _ctx(request, db)
    data = await ctx.service.overview()
    return _render(
        request,
        "overview.html",
        ctx=ctx,
        overview=data,
        recent_orders=await ctx.service.list_orders(limit=5),
        recent_activity=await ctx.service.list_audit_logs(limit=5),
    )


# --- Application data tables -------------------------------------------------


@router.get("/tables", response_class=HTMLResponse)
async def table_catalog(request: Request, db: DbDep) -> HTMLResponse:
    """List every application-owned table and its dashboard interaction mode."""
    ctx = await _ctx(request, db)
    tables = ctx.service.list_table_catalog()
    return _render(request, "tables.html", ctx=ctx, tables=tables, table_count=len(tables))


@router.get("/tables/{table_name}", response_class=HTMLResponse)
async def table_browser(
    request: Request,
    db: DbDep,
    table_name: str,
    options: BrowseDep,
    page: Annotated[int, Query(ge=1, le=1)] = 1,
) -> HTMLResponse:
    """Render one bounded, redacted page from an application table."""
    ctx = await _ctx(request, db)
    data = await ctx.service.browse_table(table_name, options)
    filters = {
        "page_size": options.page_size,
        "sort_by": data.sort_by if data else options.sort_by,
        "direction": options.direction,
        "filter_field": options.filter_field,
        "filter_value": options.filter_value,
        "start_at": options.start_at.astimezone(ADMIN_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")
        if options.start_at
        else "",
        "end_at": options.end_at.astimezone(ADMIN_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")
        if options.end_at
        else "",
    }
    base_url = request.url.path
    next_url = previous_url = None
    if data:
        shared = {key: value for key, value in filters.items() if value != ""}
        if data.next_cursor:
            next_url = (
                base_url
                + "?"
                + urlencode({**shared, "after": str(data.next_cursor), "cursor_at": data.next_at})
            )
        if data.previous_cursor:
            previous_url = (
                base_url
                + "?"
                + urlencode(
                    {**shared, "before": str(data.previous_cursor), "cursor_at": data.previous_at}
                )
            )
    return _render(
        request,
        "table_browser.html",
        ctx=ctx,
        data=data,
        filters=filters,
        next_url=next_url,
        previous_url=previous_url,
        browser_url=base_url,
    )


# --- Couriers ----------------------------------------------------------------


@router.get("/couriers/new", response_class=HTMLResponse)
async def courier_new(request: Request, db: DbDep) -> HTMLResponse:
    """Show the courier-profile creation form."""
    ctx = await _ctx(request, db)
    return _render(
        request, "courier_new.html", ctx=ctx, cities=await ctx.service.list_active_cities()
    )


@router.post("/couriers")
async def courier_create(
    request: Request,
    db: DbDep,
    csrf_token: Annotated[str, Form()],
    user_id: Annotated[uuid.UUID, Form()],
    city_of_residence: Annotated[str, Form(min_length=1, max_length=100)],
    identity_type: Annotated[str, Form()],
    identity_document: Annotated[str, Form(min_length=1, max_length=100)],
    bio: Annotated[str, Form(max_length=1000)] = "",
) -> RedirectResponse:
    """Create a profile for an existing courier user."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    profile = await ctx.service.create_courier_profile(
        admin_id=ctx.admin.id,
        user_id=user_id,
        city_of_residence=city_of_residence.strip(),
        bio=bio.strip() or None,
        identity_type=identity_type,
        identity_document=identity_document.strip(),
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/couriers/{profile.user_id}", status_code=303)


@router.get("/couriers", response_class=HTMLResponse)
async def couriers(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "courier_profiles", options)


@router.get("/couriers/{courier_id}", response_class=HTMLResponse)
async def courier_detail(request: Request, db: DbDep, courier_id: uuid.UUID) -> HTMLResponse:
    """Show a courier profile with identity numbers masked by default."""
    ctx = await _ctx(request, db)
    profile = await ctx.service.get_courier(courier_id)
    user = await ctx.service.get_user(courier_id)
    return _render(
        request,
        "courier_detail.html",
        ctx=ctx,
        profile=profile,
        user=user,
        revealed=None,
        cities=await ctx.service.list_active_cities(),
    )


@router.post("/couriers/{courier_id}/verify")
async def courier_verify(
    request: Request,
    db: DbDep,
    courier_id: uuid.UUID,
    decision: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Approve or reject a courier's verification."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.verify_courier(
        admin_id=ctx.admin.id,
        courier_user_id=courier_id,
        approve=decision == "approve",
        note=note or None,
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/couriers/{courier_id}", status_code=303)


@router.post("/couriers/{courier_id}/reveal-identity", response_class=HTMLResponse)
async def courier_reveal(
    request: Request, db: DbDep, courier_id: uuid.UUID, csrf_token: Annotated[str, Form()]
) -> HTMLResponse:
    """Reveal a courier's identity documents once and record the audit event."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    revealed = await ctx.service.reveal_identity(
        admin_id=ctx.admin.id, courier_user_id=courier_id, ip=client_ip(request)
    )
    profile = await ctx.service.get_courier(courier_id)
    user = await ctx.service.get_user(courier_id)
    response = _render(
        request,
        "courier_detail.html",
        ctx=ctx,
        profile=profile,
        user=user,
        revealed=revealed,
        cities=await ctx.service.list_active_cities(),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/couriers/{courier_id}/edit")
async def courier_edit(
    request: Request,
    db: DbDep,
    courier_id: uuid.UUID,
    csrf_token: Annotated[str, Form()],
    city_of_residence: Annotated[str, Form(min_length=1, max_length=100)],
    bio: Annotated[str, Form(max_length=1000)] = "",
) -> RedirectResponse:
    """Update the safe public fields of a courier profile."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.update_courier_profile(
        admin_id=ctx.admin.id,
        courier_user_id=courier_id,
        city_of_residence=city_of_residence.strip(),
        bio=bio.strip() or None,
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/couriers/{courier_id}", status_code=303)


@router.post("/couriers/{courier_id}/delete")
async def courier_delete(
    request: Request,
    db: DbDep,
    courier_id: uuid.UUID,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """Delete a courier profile while retaining the underlying user record."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.delete_courier_profile(
        admin_id=ctx.admin.id, user_id=courier_id, ip=client_ip(request)
    )
    return RedirectResponse("/v1/admin/admin/tables/courier_profiles", status_code=303)


# --- Orders / invoices -------------------------------------------------------


@router.get("/orders/new", response_class=HTMLResponse)
async def order_new(request: Request, db: DbDep) -> HTMLResponse:
    """Show the form for an administrator-created NEW order."""
    ctx = await _ctx(request, db)
    return _render(
        request,
        "order_new.html",
        ctx=ctx,
        today=date.today().isoformat(),
        cities=await ctx.service.list_active_cities(),
    )


@router.post("/orders")
async def order_create(
    request: Request,
    db: DbDep,
    csrf_token: Annotated[str, Form()],
    customer_id: Annotated[uuid.UUID, Form()],
    delivery_city: Annotated[str, Form(min_length=1, max_length=100)],
    delivery_date: Annotated[date, Form()],
    description: Annotated[str, Form(max_length=5000)] = "",
    delivery_address_note: Annotated[str, Form(max_length=255)] = "",
) -> RedirectResponse:
    """Create a NEW order on behalf of an active customer."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    order_id = await ctx.service.create_order(
        admin_id=ctx.admin.id,
        customer_id=customer_id,
        description=description.strip() or None,
        delivery_city=delivery_city.strip(),
        delivery_date=delivery_date,
        delivery_address_note=delivery_address_note.strip() or None,
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/orders/{order_id}", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
async def orders(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "orders", options)


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, db: DbDep, order_id: uuid.UUID) -> HTMLResponse:
    """Show an order."""
    ctx = await _ctx(request, db)
    order = await ctx.service.get_order(order_id)
    return _render(
        request,
        "order_detail.html",
        ctx=ctx,
        order=order,
        cities=await ctx.service.list_active_cities(),
    )


@router.post("/orders/{order_id}/edit")
async def order_edit(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    csrf_token: Annotated[str, Form()],
    delivery_city: Annotated[str, Form(min_length=1, max_length=100)],
    delivery_date: Annotated[date, Form()],
    description: Annotated[str, Form(max_length=5000)] = "",
    delivery_address_note: Annotated[str, Form(max_length=255)] = "",
) -> RedirectResponse:
    """Update non-financial order details while the order is still editable."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.update_order_details(
        admin_id=ctx.admin.id,
        order_id=order_id,
        description=description.strip() or None,
        delivery_city=delivery_city.strip(),
        delivery_date=delivery_date,
        delivery_address_note=delivery_address_note.strip() or None,
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/orders/{order_id}", status_code=303)


@router.post("/orders/{order_id}/delete")
async def order_delete(
    request: Request,
    db: DbDep,
    order_id: uuid.UUID,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """Permanently delete an unassigned NEW order."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.delete_order(admin_id=ctx.admin.id, order_id=order_id, ip=client_ip(request))
    return RedirectResponse("/v1/admin/admin/tables/orders", status_code=303)


@router.get("/invoices", response_class=HTMLResponse)
async def invoices(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "invoices", options)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(request: Request, db: DbDep, invoice_id: uuid.UUID) -> HTMLResponse:
    """Show an invoice."""
    ctx = await _ctx(request, db)
    invoice = await ctx.service.get_invoice(invoice_id)
    return _render(request, "invoice_detail.html", ctx=ctx, invoice=invoice)


# --- Promos -----------------------------------------------------------------


@router.get("/promos", response_class=HTMLResponse)
async def promos(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "promos", options)


@router.get("/promos/{promo_id}", response_class=HTMLResponse)
async def promo_detail(request: Request, db: DbDep, promo_id: uuid.UUID) -> HTMLResponse:
    """Show a promo."""
    ctx = await _ctx(request, db)
    promo = await ctx.service.get_promo(promo_id)
    return _render(request, "promo_detail.html", ctx=ctx, promo=promo)


@router.get("/promos/{promo_id}/redemptions", response_class=HTMLResponse)
async def promo_redemptions(
    request: Request, db: DbDep, promo_id: uuid.UUID, options: BrowseDep
) -> HTMLResponse:
    """List a promo's redemptions."""
    scoped = replace(options, scope_field="promo_id", scope_value=str(promo_id))
    return await table_browser(request, db, "promo_redemptions", scoped)


# --- Disputes / withdrawals / wallets / topups -------------------------------


@router.get("/disputes", response_class=HTMLResponse)
async def disputes(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "disputes", options)


@router.get("/disputes/{dispute_id}", response_class=HTMLResponse)
async def dispute_detail(request: Request, db: DbDep, dispute_id: uuid.UUID) -> HTMLResponse:
    """Show a dispute (resolution moves money and belongs to the ledger service)."""
    ctx = await _ctx(request, db)
    dispute = await ctx.service.get_dispute(dispute_id)
    return _render(request, "dispute_detail.html", ctx=ctx, dispute=dispute)


@router.get("/withdrawals", response_class=HTMLResponse)
async def withdrawals(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "withdrawals", options)


@router.get("/wallets", response_class=HTMLResponse)
async def wallets(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "wallets", options)


@router.get("/wallets/{wallet_id}", response_class=HTMLResponse)
async def wallet_detail(request: Request, db: DbDep, wallet_id: uuid.UUID) -> HTMLResponse:
    """Show a wallet."""
    ctx = await _ctx(request, db)
    wallet = await ctx.service.get_wallet(wallet_id)
    return _render(request, "wallet_detail.html", ctx=ctx, wallet=wallet)


@router.get("/topups", response_class=HTMLResponse)
async def topups(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List records with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "wallet_topups", options)


# --- Users -------------------------------------------------------------------


@router.get("/users", response_class=HTMLResponse)
async def users(request: Request, db: DbDep, options: BrowseDep) -> HTMLResponse:
    """List users with shared filters, sorting, and pagination."""
    return await table_browser(request, db, "users", options)


@router.get("/users/new", response_class=HTMLResponse)
async def user_new(request: Request, db: DbDep) -> HTMLResponse:
    """Show the dashboard user-creation form."""
    ctx = await _ctx(request, db)
    return _render(
        request, "user_new.html", ctx=ctx, user_roles=(UserRole.CUSTOMER, UserRole.COURIER)
    )


@router.post("/users")
async def user_create(
    request: Request,
    db: DbDep,
    csrf_token: Annotated[str, Form()],
    phone: Annotated[str, Form(min_length=1, max_length=20)],
    role: Annotated[UserRole, Form()],
    full_name: Annotated[str, Form(max_length=120)] = "",
    email: Annotated[str, Form(max_length=255)] = "",
) -> RedirectResponse:
    """Create a customer or courier user from the dashboard."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    user = await ctx.service.create_user(
        admin_id=ctx.admin.id,
        phone=phone.strip(),
        full_name=full_name.strip() or None,
        email=email.strip().lower() or None,
        role=role,
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/users/{user.id}", status_code=303)


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, db: DbDep, user_id: uuid.UUID) -> HTMLResponse:
    """Show a user."""
    ctx = await _ctx(request, db)
    user = await ctx.service.get_user(user_id)
    return _render(
        request,
        "user_detail.html",
        ctx=ctx,
        user=user,
    )


@router.post("/users/{user_id}/ban")
async def user_ban(
    request: Request, db: DbDep, user_id: uuid.UUID, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    """Ban a user after CSRF validation and record the audit event."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.set_user_banned(
        admin_id=ctx.admin.id, user_id=user_id, banned=True, ip=client_ip(request)
    )
    return RedirectResponse(f"/v1/admin/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/unban")
async def user_unban(
    request: Request, db: DbDep, user_id: uuid.UUID, csrf_token: Annotated[str, Form()]
) -> RedirectResponse:
    """Unban a user after CSRF validation and record the audit event."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.set_user_banned(
        admin_id=ctx.admin.id, user_id=user_id, banned=False, ip=client_ip(request)
    )
    return RedirectResponse(f"/v1/admin/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/edit")
async def user_edit(
    request: Request,
    db: DbDep,
    user_id: uuid.UUID,
    csrf_token: Annotated[str, Form()],
    phone: Annotated[str, Form(max_length=20)] = "",
    full_name: Annotated[str, Form(max_length=120)] = "",
    email: Annotated[str, Form(max_length=255)] = "",
) -> RedirectResponse:
    """Update a user's non-authentication profile fields."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.update_user_profile(
        admin_id=ctx.admin.id,
        user_id=user_id,
        full_name=full_name.strip() or None,
        email=email.strip().lower() or None,
        phone=phone.strip() or None,
        ip=client_ip(request),
    )
    return RedirectResponse(f"/v1/admin/admin/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/delete")
async def user_delete(
    request: Request,
    db: DbDep,
    user_id: uuid.UUID,
    csrf_token: Annotated[str, Form()],
) -> RedirectResponse:
    """Soft-delete a user and revoke their current access."""
    ctx = await _ctx(request, db)
    verify_csrf(ctx, csrf_token, get_settings_from(request))
    await ctx.service.delete_user(admin_id=ctx.admin.id, user_id=user_id, ip=client_ip(request))
    return RedirectResponse("/v1/admin/admin/tables/users", status_code=303)


# --- Audit logs --------------------------------------------------------------


@router.get("/audit-logs", response_class=HTMLResponse)
@router.get("/audit-logs/{view}", response_class=HTMLResponse)
async def audit_logs(
    request: Request,
    db: DbDep,
    view: Literal["users", "admin", "system"] = "system",
    actor_user_id: OptionalFilterUuid = None,
    action: str | None = Query(default=None, max_length=100),
    entity_type: str | None = Query(default=None, max_length=50),
    activity_id: OptionalFilterUuid = None,
    activity_name: str | None = Query(default=None, max_length=100),
    start_at: OptionalFilterDatetime = None,
    end_at: OptionalFilterDatetime = None,
    sort: Literal["newest", "oldest"] = "newest",
    page_size: int = Query(default=25, ge=25, le=100),
    before_at: datetime | None = None,
    before_id: uuid.UUID | None = None,
) -> HTMLResponse:
    """Load only the selected activity tab with bounded cursor pagination."""
    ctx = await _ctx(request, db)
    if page_size not in {25, 50, 100}:
        raise HTTPException(status_code=422, detail="Page size must be 25, 50, or 100.")
    if (before_at is None) != (before_id is None):
        raise HTTPException(status_code=400, detail="Both audit cursor fields are required.")
    start_at, end_at = admin_input(start_at), admin_input(end_at)
    before_at = before_at.replace(tzinfo=UTC) if before_at and not before_at.tzinfo else before_at
    if start_at and end_at and start_at > end_at:
        raise HTTPException(status_code=400, detail="Start time must not exceed end time.")
    categories = {
        "users": ("USER", "CUSTOMER", "COURIER"),
        "admin": ("ADMIN",),
        "system": ("SYSTEM",),
    }[view]
    rows = await ctx.service.list_audit_logs(
        limit=page_size + 1,
        actor_categories=categories,
        actor_user_id=actor_user_id,
        action=action or None,
        entity_type=entity_type or None,
        activity_id=activity_id,
        activity_name=activity_name or None,
        start_at=start_at,
        end_at=end_at,
        oldest_first=sort == "oldest",
        before_at=before_at,
        before_id=before_id,
    )
    filters = {
        "actor_user_id": str(actor_user_id) if actor_user_id else "",
        "action": action or "",
        "entity_type": entity_type or "",
        "activity_id": str(activity_id) if activity_id else "",
        "activity_name": activity_name or "",
        "start_at": start_at.astimezone(ADMIN_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")
        if start_at
        else "",
        "end_at": end_at.astimezone(ADMIN_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S") if end_at else "",
        "sort": sort,
        "page_size": page_size,
    }
    base_url = "/v1/admin/admin/audit-logs"
    shared_filters = {key: value for key, value in filters.items() if value}
    view_urls = {
        name: base_url + "?" + urlencode({**shared_filters, "view": name})
        for name in ("system", "admin", "users")
    }
    next_url = None
    if len(rows) > page_size:
        last = rows[page_size - 1]
        next_url = (
            base_url
            + "?"
            + urlencode(
                {
                    **shared_filters,
                    "view": view,
                    "before_at": last.created_at.isoformat(),
                    "before_id": str(last.id),
                }
            )
        )
    return _render(
        request,
        "audit_logs.html",
        ctx=ctx,
        logs=rows[:page_size],
        actors=await ctx.service.audit_actors(rows[:page_size]),
        entities=await ctx.service.audit_entities(rows[:page_size]),
        choices=await ctx.service.audit_filter_choices(categories),
        next_url=next_url,
        filters=filters,
        active_view=view,
        view_urls=view_urls,
        clear_url=base_url + "?" + urlencode({"view": view}),
    )
