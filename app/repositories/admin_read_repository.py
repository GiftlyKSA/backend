"""Read-only aggregate queries for the admin dashboard (SPEC SECTION 18.3).

These back the overview and the list/detail pages. Kept in the repository layer so the
admin service — and therefore the dashboard — never issues a raw query itself.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.admin_time import admin_datetime
from app.models import (
    Dispute,
    Invoice,
    Order,
    PaymentIntent,
    Wallet,
    Withdrawal,
)
from app.models.base import Base
from app.models.enums import (
    DisputeStatus,
    OrderStatus,
    WithdrawalStatus,
)
from app.repositories.admin_browse_query import (
    BrowseOptions,
    browse_query,
    date_columns,
    scalar_column,
)
from app.repositories.admin_table_repository import AdminTableRepository, record_label

_PAGE_SIZE = 50
_ADMIN_VISIBLE_USER_COLUMNS = {"phone", "email", "full_name", "date_of_birth"}
_REDACTED_MARKERS = (
    "encrypted",
    "token",
    "secret",
    "hash",
    "fingerprint",
    "password",
)
_REDACTED_COLUMNS = {
    "phone",
    "email",
    "full_name",
    "date_of_birth",
    "ip_address",
    "user_agent",
    "delivery_address_note",
    "iban_last4",
    "gateway_payment_url",
}


@dataclass(frozen=True)
class AdminTableInfo:
    """A dashboard-visible application table and its permitted interaction mode."""

    name: str
    editable: bool

    @property
    def label(self) -> str:
        """Return a human-readable label for the table."""
        return self.name.replace("_", " ").title()


@dataclass(frozen=True)
class AdminTableRow:
    """A redacted database row ready for the server-rendered table browser."""

    cells: list[str]
    edit_url: str | None
    detail_url: str | None = None


@dataclass(frozen=True)
class AdminTablePage:
    """One bounded page of a table-browser result."""

    table: AdminTableInfo
    columns: list[str]
    edit_column: str | None
    rows: list[AdminTableRow]
    next_cursor: uuid.UUID | None
    previous_cursor: uuid.UUID | None
    next_at: str = ""
    previous_at: str = ""
    sort_fields: list[str] = field(default_factory=list)
    filter_fields: list[str] = field(default_factory=list)
    sort_by: str = ""


def table_page_query(
    table_name: str, *, after: uuid.UUID | None = None, before: uuid.UUID | None = None
) -> Select[Any]:
    """Page on the indexed UUID key, with a unique and stable ordering."""
    table = Base.metadata.tables[table_name]
    key = next(iter(table.primary_key.columns))
    query = select(table)
    if after is not None:
        query = query.where(key < after)
    if before is not None:
        query = query.where(key > before)
    return query.order_by(key.asc() if before is not None else key.desc()).limit(_PAGE_SIZE + 1)


class AdminReadRepository:
    """Aggregate and list reads across orders, invoices, disputes, and money."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session."""
        self._session = session

    def list_table_catalog(self) -> list[AdminTableInfo]:
        """Return every application-owned table, excluding database extension tables."""
        return [AdminTableInfo(name=name, editable=True) for name in sorted(Base.metadata.tables)]

    async def browse_table(self, table_name: str, options: BrowseOptions) -> AdminTablePage | None:
        """Fetch one filtered page and preserve redaction on every table."""
        table = Base.metadata.tables.get(table_name)
        if table is None:
            return None
        key = next(iter(table.primary_key.columns)).name
        fields = [
            c.name
            for c in table.columns
            if scalar_column(c) and self._display_value(table_name, c.name, "x") != "••••••"
        ]
        dates = date_columns(table)
        sort_by = options.sort_by or ("created_at" if "created_at" in dates else key)
        result = await self._session.execute(browse_query(table, options, fields))
        mappings = list(result.mappings())
        has_more = len(mappings) > options.page_size
        visible = mappings[: options.page_size]
        if options.before:
            visible.reverse()
        columns = [c.name for c in table.columns]
        related = await self._related_labels(table_name, visible)
        sections = {
            "users": "users",
            "courier_profiles": "couriers",
            "orders": "orders",
            "invoices": "invoices",
            "promos": "promos",
            "disputes": "disputes",
            "wallets": "wallets",
        }
        rows = [
            AdminTableRow(
                cells=[self._browse_cell(table_name, c, row, key, related) for c in columns],
                edit_url=self._edit_url(table_name, row),
                detail_url=f"/v1/admin/admin/{sections[table_name]}/{row[key]}"
                if table_name in sections
                else None,
            )
            for row in visible
        ]
        next_row = visible[-1] if visible and (has_more or options.before) else None
        previous_row = (
            visible[0] if visible and (options.after or (options.before and has_more)) else None
        )
        return AdminTablePage(
            table=AdminTableInfo(table_name, True),
            columns=columns,
            edit_column=key,
            rows=rows,
            next_cursor=next_row[key] if next_row is not None else None,
            previous_cursor=previous_row[key] if previous_row is not None else None,
            next_at=str(next_row[sort_by]) if next_row is not None and sort_by != key else "",
            previous_at=str(previous_row[sort_by])
            if previous_row is not None and sort_by != key
            else "",
            sort_fields=[*dates, key],
            filter_fields=fields,
            sort_by=sort_by,
        )

    def _browse_cell(
        self,
        table_name: str,
        column: str,
        row: RowMapping,
        key: str,
        related: dict[tuple[str, uuid.UUID], str],
    ) -> str:
        if column == key:
            return record_label(Base.metadata.tables[table_name], dict(row))
        value = row[column]
        if isinstance(value, uuid.UUID) and (column, value) in related:
            return related[(column, value)]
        return self._display_value(table_name, column, value)

    async def _related_labels(
        self, table_name: str, rows: Sequence[RowMapping]
    ) -> dict[tuple[str, uuid.UUID], str]:
        targets = {
            c.name: next(iter(c.foreign_keys)).column.table.name
            for c in Base.metadata.tables[table_name].columns
            if c.foreign_keys
        }
        records: dict[str, set[uuid.UUID]] = {}
        for name, target in targets.items():
            records.setdefault(target, set()).update(
                value for row in rows if isinstance(value := row[name], uuid.UUID)
            )
        labels = await AdminTableRepository(self._session).labels_for_records(records)
        return {
            (name, record_id): label
            for name, target in targets.items()
            for (table, record_id), label in labels.items()
            if target == table
        }

    async def list_table_page(
        self, table_name: str, *, after: uuid.UUID | None = None, before: uuid.UUID | None = None
    ) -> AdminTablePage | None:
        """Return a bounded, redacted page for a known application table.

        The table name is resolved only from SQLAlchemy metadata, never interpolated into
        SQL. Sensitive values remain hidden even from this broad administrative browser;
        dedicated audited admin actions reveal restricted identity/IBAN data.
        """
        table = Base.metadata.tables.get(table_name)
        if table is None:
            return None
        info = AdminTableInfo(name=table_name, editable=True)
        columns = [column.name for column in table.columns]
        if after is not None and before is not None:
            raise ValueError("Choose one table cursor.")
        result = await self._session.execute(
            table_page_query(table_name, after=after, before=before)
        )
        mappings = list(result.mappings())
        has_more = len(mappings) > _PAGE_SIZE
        visible = mappings[:_PAGE_SIZE]
        if before is not None:
            visible.reverse()
        key_name = next(iter(table.primary_key.columns)).name
        rows = [
            AdminTableRow(
                cells=[self._display_value(table_name, column, row[column]) for column in columns],
                edit_url=self._edit_url(table_name, row),
            )
            for row in visible
        ]
        return AdminTablePage(
            table=info,
            columns=columns,
            edit_column=next(iter(table.primary_key.columns)).name,
            rows=rows,
            next_cursor=(
                visible[-1][key_name] if visible and (before is not None or has_more) else None
            ),
            previous_cursor=(
                visible[0][key_name]
                if visible and (after is not None or (before is not None and has_more))
                else None
            ),
        )

    @staticmethod
    def _edit_url(table_name: str, row: Any) -> str | None:
        """Return the generic record editor URL for an application table."""
        key = next(iter(Base.metadata.tables[table_name].primary_key.columns)).name
        return f"/v1/admin/admin/tables/{table_name}/{row[key]}/edit"

    @staticmethod
    def _display_value(table_name: str, column: str, value: object) -> str:
        """Format a DB value for display while redacting sensitive columns."""
        normalized = column.lower()
        if (
            normalized in _REDACTED_COLUMNS
            and not (table_name == "users" and normalized in _ADMIN_VISIBLE_USER_COLUMNS)
        ) or any(marker in normalized for marker in _REDACTED_MARKERS):
            return "••••••"
        if value is None:
            return "—"
        if isinstance(value, Enum):
            return str(value.value)
        if isinstance(value, datetime):
            return admin_datetime(value)
        if isinstance(value, (date, uuid.UUID, Decimal)):
            return str(value)
        if isinstance(value, (dict, list)):
            return AdminReadRepository._truncate(json.dumps(value, default=str, ensure_ascii=False))
        return AdminReadRepository._truncate(str(value))

    @staticmethod
    def _truncate(value: str, limit: int = 160) -> str:
        """Keep browser cells compact without changing the underlying read query."""
        return value if len(value) <= limit else f"{value[: limit - 1]}…"

    async def order_counts_by_status(self) -> dict[str, int]:
        """Return a map of order status -> count."""
        rows = await self._session.execute(
            select(Order.status, func.count()).group_by(Order.status)
        )
        return {str(status): count for status, count in rows.all()}

    async def daily_order_counts(self, start_date: date, end_date: date) -> dict[date, int]:
        """Count created orders by UTC day within an inclusive date range."""
        utc_day = func.date(func.timezone("UTC", Order.created_at))
        rows = await self._session.execute(
            select(utc_day, func.count())
            .where(
                Order.created_at >= datetime.combine(start_date, time.min, UTC),
                Order.created_at < datetime.combine(end_date + timedelta(days=1), time.min, UTC),
            )
            .group_by(utc_day)
            .order_by(utc_day)
        )
        return {day: count for day, count in rows.all()}

    async def open_dispute_count(self) -> int:
        """Return the number of open disputes."""
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(Dispute)
                .where(Dispute.status == DisputeStatus.OPEN)
            )
        ) or 0

    async def pending_withdrawal_count(self) -> int:
        """Return the number of withdrawals awaiting processing."""
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(Withdrawal)
                .where(Withdrawal.status == WithdrawalStatus.REQUESTED)
            )
        ) or 0

    async def system_wallet_balances(self) -> dict[str, Decimal]:
        """Return a map of system wallet type -> balance."""
        rows = await self._session.execute(
            select(Wallet.type, Wallet.balance).where(Wallet.user_id.is_(None))
        )
        return {str(wtype): balance for wtype, balance in rows.all()}

    async def list_orders(self, status: OrderStatus | None = None, limit: int = 50) -> list[Order]:
        """Return orders, newest first, optionally filtered by status."""
        query = select(Order).order_by(Order.created_at.desc()).limit(limit)
        if status is not None:
            query = query.where(Order.status == status)
        return list(await self._session.scalars(query))

    async def get_order(self, order_id: uuid.UUID) -> Order | None:
        """Return an order by id, or None."""
        return await self._session.get(Order, order_id)

    async def list_invoices(self, limit: int = 50) -> list[Invoice]:
        """Return invoices, newest first."""
        return list(
            await self._session.scalars(
                select(Invoice).order_by(Invoice.created_at.desc()).limit(limit)
            )
        )

    async def get_invoice(self, invoice_id: uuid.UUID) -> Invoice | None:
        """Return an invoice by id, or None."""
        return await self._session.get(Invoice, invoice_id)

    async def list_disputes(
        self, status: DisputeStatus | None = None, limit: int = 50
    ) -> list[Dispute]:
        """Return disputes, newest first, optionally filtered by status."""
        query = select(Dispute).order_by(Dispute.created_at.desc()).limit(limit)
        if status is not None:
            query = query.where(Dispute.status == status)
        return list(await self._session.scalars(query))

    async def get_dispute(self, dispute_id: uuid.UUID) -> Dispute | None:
        """Return a dispute by id, or None."""
        return await self._session.get(Dispute, dispute_id)

    async def list_withdrawals(
        self, status: WithdrawalStatus | None = None, limit: int = 50
    ) -> list[Withdrawal]:
        """Return withdrawals, newest first, optionally filtered by status."""
        query = select(Withdrawal).order_by(Withdrawal.created_at.desc()).limit(limit)
        if status is not None:
            query = query.where(Withdrawal.status == status)
        return list(await self._session.scalars(query))

    async def get_withdrawal(self, withdrawal_id: uuid.UUID) -> Withdrawal | None:
        """Return a withdrawal by id, or None."""
        return await self._session.get(Withdrawal, withdrawal_id)

    async def list_wallets(self, limit: int = 50) -> list[Wallet]:
        """Return system wallets and a page of user wallets, system first."""
        query = (
            select(Wallet)
            .order_by(Wallet.user_id.is_(None).desc(), Wallet.created_at.desc())
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def get_wallet(self, wallet_id: uuid.UUID) -> Wallet | None:
        """Return a wallet by id, or None."""
        return await self._session.get(Wallet, wallet_id)

    async def list_topups(self, limit: int = 50) -> list[PaymentIntent]:
        """Return wallet top-up intents, newest first."""
        query = (
            select(PaymentIntent)
            .where(PaymentIntent.purpose == "WALLET_TOPUP")
            .order_by(PaymentIntent.created_at.desc())
            .limit(limit)
        )
        return list(await self._session.scalars(query))
