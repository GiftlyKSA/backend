"""Bounded metadata-based admin table access."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Column,
    Select,
    String,
    Table,
    UniqueConstraint,
    case,
    cast,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models import Base

CHOICE_LIMIT = 25
_LABEL_COLUMNS = ("full_name", "phone", "title", "code", "name", "delivery_city", "type", "status")


def get_table(name: str) -> Table:
    """Resolve application tables without accepting SQL identifiers from clients."""
    table = Base.metadata.tables.get(name)
    if table is None:
        raise NotFoundError("Table not found.")
    return table


def primary_key(table: Table) -> Column[Any]:
    """Return the single primary key used by application tables."""
    return next(iter(table.primary_key.columns))


def relationship_column(table_name: str, field: str) -> Column[Any]:
    """Resolve a declared foreign key only."""
    column = get_table(table_name).c.get(field)
    if column is None or not column.foreign_keys:
        raise NotFoundError("Relationship not found.")
    return column


def label_columns(table: Table) -> list[Column[Any]]:
    """Select explicit, non-secret label fields without loading related objects."""
    return [table.c[name] for name in _LABEL_COLUMNS if name in table.c]


def record_label(table: Table, row: Mapping[str, Any]) -> str:
    """Describe a choice with readable values and an unambiguous key."""
    values = [str(row[column.name])[:80] for column in label_columns(table) if row[column.name]]
    return " · ".join([*values, str(row[primary_key(table).name])])


def relationship_query(
    table_name: str,
    field: str,
    *,
    record_id: uuid.UUID | None = None,
    search: str = "",
    after: uuid.UUID | None = None,
) -> Select[Any]:
    """Build a keyset-paginated choice query, excluding claimed unique relationships."""
    column = relationship_column(table_name, field)
    target = next(iter(column.foreign_keys)).column
    table = get_table(table_name)
    query = select(target, *label_columns(target.table))
    if target.table.name == "users":
        query = query.where(target.table.c.deleted_at.is_(None))
        role = _user_role(table_name, field)
        if role:
            query = query.where(target.table.c.role == role)
        if table_name == "orders" and field == "customer_id":
            query = query.where(target.table.c.status == "ACTIVE")
    for predicate in _unique_predicates(table, column):
        claimed = select(1).select_from(table).where(column == target)
        if predicate is not None:
            claimed = claimed.where(predicate)
        if record_id is not None:
            claimed = claimed.where(primary_key(table) != record_id)
        query = query.where(~exists(claimed))
    if record_id is not None and query.whereclause is not None:
        current = select(column).where(primary_key(table) == record_id).scalar_subquery()
        query = select(target, *label_columns(target.table)).where(
            or_(query.whereclause, target == current)
        )
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                *[
                    cast(item, String).ilike(f"%{escaped}%", escape="\\")
                    for item in [target, *label_columns(target.table)]
                ]
            )
        )
    if after is not None:
        query = query.where(target > after)
    return query.order_by(target).limit(CHOICE_LIMIT + 1)


def _user_role(table: str, field: str) -> str | None:
    if field.endswith("admin_id") or field == "admin_user_id":
        return "ADMIN"
    if field == "customer_id":
        return "CUSTOMER"
    if field == "courier_id" or (table == "courier_profiles" and field == "user_id"):
        return "COURIER"
    return None


def _unique_predicates(table: Table, column: Column[Any]) -> list[Any]:
    predicates: list[Any] = []
    if column.primary_key or any(
        isinstance(constraint, UniqueConstraint)
        and list(constraint.columns.keys()) == [column.name]
        for constraint in table.constraints
    ):
        predicates.append(None)
    for index in table.indexes:
        if index.unique and list(index.columns.keys()) == [column.name]:
            predicates.append(index.dialect_options["postgresql"].get("where"))
    return predicates


class AdminTableRepository:
    """Execute table and relationship queries for admin services."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the request transaction."""
        self.session = session

    async def authorize_maintenance(self, admin_id: uuid.UUID, session_id: uuid.UUID) -> None:
        """Enable trigger exceptions locally; the database independently checks the session."""
        await self.session.execute(
            select(
                func.set_config("giftly.admin_actor", str(admin_id), True),
                func.set_config("giftly.admin_session", str(session_id), True),
            )
        )
        if not await self.session.scalar(select(func.giftly_admin_maintenance_allowed())):
            raise ForbiddenError("An active administrator session is required.")

    async def clear_maintenance(self) -> None:
        """Remove maintenance authority before subsequent work in this transaction."""
        await self.session.execute(
            select(
                func.set_config("giftly.admin_actor", "", True),
                func.set_config("giftly.admin_session", "", True),
            )
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Roll back rejected writes and their audit entries without exposing SQL or values."""
        try:
            async with self.session.begin_nested():
                yield
        except DBAPIError as exc:
            code = getattr(exc.orig, "sqlstate", "") or ""
            if code.startswith(("22", "23")) or code == "P0001":
                raise ConflictError(
                    "The database rejected this change. Check required and unique values, "
                    "related records, and protected-record constraints. No changes were saved."
                ) from exc
            raise

    async def get_record(
        self, table_name: str, record_id: uuid.UUID, *, lock: bool = False
    ) -> dict[str, Any]:
        """Read one record, optionally locking it for an audited write."""
        table = get_table(table_name)
        columns = [
            case(
                (column.is_(None), None),
                else_=func.concat(func.ST_X(column), ", ", func.ST_Y(column)),
            ).label(column.name)
            if isinstance(column.type, Geometry)
            else column
            for column in table.c
        ]
        query = select(*columns).where(primary_key(table) == record_id)
        if lock:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        if row is None:
            raise NotFoundError("Record not found.")
        return dict(row)

    async def selected_labels(self, table: Table, row: dict[str, Any]) -> dict[str, str]:
        """Batch selected labels by target table, not by displayed row or field."""
        grouped: dict[str, set[uuid.UUID]] = {}
        targets: dict[str, Column[Any]] = {}
        for column in table.c:
            if column.foreign_keys and row.get(column.name) is not None:
                target = next(iter(column.foreign_keys)).column
                targets[column.name] = target
                grouped.setdefault(target.table.name, set()).add(row[column.name])
        labels: dict[tuple[str, str], str] = {}
        for name, keys in grouped.items():
            target_table = get_table(name)
            key = primary_key(target_table)
            result = await self.session.execute(
                select(key, *label_columns(target_table)).where(key.in_(keys))
            )
            for related in result.mappings():
                labels[(name, str(related[key.name]))] = record_label(target_table, dict(related))
        return {
            name: labels.get((target.table.name, str(row[name])), str(row[name]))
            for name, target in targets.items()
        }

    async def save(
        self, table: Table, record_id: uuid.UUID, values: dict[str, Any], *, creating: bool
    ) -> None:
        """Write validated values using bound parameters."""
        key = primary_key(table)
        if creating:
            await self.session.execute(insert(table).values(**{**values, key.name: record_id}))
        elif values:
            await self.session.execute(update(table).where(key == record_id).values(**values))

    async def remove(self, table: Table, record_id: uuid.UUID) -> None:
        """Delete a selected row while honoring database referential and trigger constraints."""
        await self.session.execute(delete(table).where(primary_key(table) == record_id))

    async def choices(
        self,
        table_name: str,
        field: str,
        *,
        record_id: uuid.UUID | None = None,
        search: str = "",
        after: uuid.UUID | None = None,
    ) -> tuple[list[dict[str, str]], str | None]:
        """Fetch one page in a single query, without per-option relationship loads."""
        column = relationship_column(table_name, field)
        target = next(iter(column.foreign_keys)).column
        result = await self.session.execute(
            relationship_query(table_name, field, record_id=record_id, search=search, after=after)
        )
        rows = list(result.mappings())
        items = [
            {"value": str(row[target.name]), "label": record_label(target.table, dict(row))}
            for row in rows[:CHOICE_LIMIT]
        ]
        cursor = items[-1]["value"] if len(rows) > CHOICE_LIMIT else None
        return items, cursor
