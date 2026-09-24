"""Authenticated HTML CRUD routes for all application tables."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import (
    AdminContext,
    client_ip,
    get_db,
    get_settings_from,
    require_admin,
    require_step_up,
    verify_csrf,
)
from app.core.exceptions import ConflictError, DomainError, ValidationDomainError

router = APIRouter()
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
DbDep = Annotated[AsyncSession, Depends(get_db)]
_MAX_FORM_BYTES = 262_144


async def _submitted(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise ValidationDomainError("Submit the dashboard form without file attachments.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_FORM_BYTES:
            raise ValidationDomainError("The submitted form is too large.")
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=128)
    except (ValueError, UnicodeError) as exc:
        raise ValidationDomainError("Invalid form data.") from exc
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValidationDomainError("Duplicate form fields are not allowed.")
    return values


async def _form_response(
    request: Request,
    ctx: AdminContext,
    table_name: str,
    record_id: uuid.UUID | None = None,
    *,
    error: DomainError | None = None,
    submitted: dict[str, str] | None = None,
) -> HTMLResponse:
    form = await ctx.tables.form(table_name, record_id)
    if submitted and not isinstance(error, ConflictError):
        form = replace(
            form,
            fields=[
                replace(field, value=submitted.get(field.name, field.value), selected_label="")
                if not field.secret and not field.readonly
                else field
                for field in form.fields
            ],
        )
    return _TEMPLATES.TemplateResponse(
        request,
        "table_form.html",
        {"ctx": ctx, "form": form, "error": error.message if error else None},
        status_code=error.status_code if error else 200,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/tables/{table_name}/new", response_class=HTMLResponse)
async def new_record(request: Request, db: DbDep, table_name: str) -> HTMLResponse:
    """Show a typed create form for any application table."""
    ctx = await require_admin(request, db)
    return await _form_response(request, ctx, table_name)


@router.get("/tables/{table_name}/{record_id}/edit", response_class=HTMLResponse)
async def edit_record(
    request: Request, db: DbDep, table_name: str, record_id: uuid.UUID
) -> HTMLResponse:
    """Show one record with write-only secrets and pre-selected relationships."""
    ctx = await require_admin(request, db)
    return await _form_response(request, ctx, table_name, record_id)


async def _mutate(
    request: Request,
    db: AsyncSession,
    table_name: str,
    record_id: uuid.UUID | None,
    *,
    deleting: bool = False,
) -> Response:
    ctx = await require_admin(request, db)
    submitted = await _submitted(request)
    verify_csrf(ctx, submitted.pop("csrf_token", ""), get_settings_from(request))
    await require_step_up(ctx)
    revision = submitted.pop("revision", "")
    try:
        if deleting:
            if submitted != {"confirm_delete": "yes"} or record_id is None:
                raise ValidationDomainError("Confirm deletion before continuing.")
            await ctx.tables.delete(
                table_name,
                record_id,
                admin_id=ctx.admin.id,
                session_id=ctx.session_row.id,
                revision=revision,
                ip=client_ip(request),
            )
            target = f"/admin/tables/{table_name}"
        else:
            saved_id = await ctx.tables.save(
                table_name,
                submitted,
                admin_id=ctx.admin.id,
                session_id=ctx.session_row.id,
                record_id=record_id,
                revision=revision,
                ip=client_ip(request),
            )
            target = f"/admin/tables/{table_name}/{saved_id}/edit"
    except DomainError as exc:
        return await _form_response(
            request, ctx, table_name, record_id, error=exc, submitted=submitted
        )
    return RedirectResponse(target, status_code=303)


@router.post("/tables/{table_name}/new")
async def create_record(request: Request, db: DbDep, table_name: str) -> Response:
    """Create a row after admin authentication, CSRF, and password confirmation."""
    return await _mutate(request, db, table_name, None)


@router.post("/tables/{table_name}/{record_id}/edit")
async def update_record(
    request: Request, db: DbDep, table_name: str, record_id: uuid.UUID
) -> Response:
    """Update a row with server-side field validation and audit logging."""
    return await _mutate(request, db, table_name, record_id)


@router.post("/tables/{table_name}/{record_id}/delete")
async def delete_record(
    request: Request, db: DbDep, table_name: str, record_id: uuid.UUID
) -> Response:
    """Delete a confirmed record while retaining database integrity protections."""
    return await _mutate(request, db, table_name, record_id, deleting=True)
