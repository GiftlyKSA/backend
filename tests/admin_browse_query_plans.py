"""Compare admin browse SQL on session-local synthetic financial tables.

GIFTLY_PERF_DATABASE_URL must target disposable loopback PostgreSQL16.
No application tables, triggers or real data are read or changed.
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from app.models import Invoice, Transaction, WalletTopup
from app.repositories.admin_browse_query import BrowseOptions, browse_query
from sqlalchemy import ForeignKeyConstraint, MetaData
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from tests.financial_query_plans import measure, sql

MODELS = (Invoice, Transaction, WalletTopup)
ROWS = 100_000
SEEDS = {
    "invoices": "(order_id,issued_by_courier_id,created_at) SELECT "
    "gen_random_uuid(),gen_random_uuid(),",
    "transactions": "(wallet_id,amount,type,correlation_id,balance_after,created_at) "
    "SELECT gen_random_uuid(),1,'TOPUP',gen_random_uuid(),0,",
    "wallet_topups": "(user_id,wallet_id,payment_intent_id,amount,created_at) "
    "SELECT gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),100,",
}


def seed(name, count):
    return (
        f"INSERT INTO {name} {SEEDS[name]} '2020-01-01'::timestamptz"
        f"+(i/10)*interval '1 minute' FROM generate_series(1,{count}) i"
    )


async def create_tables(connection):
    metadata = MetaData()
    enums = set()
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        table._prefixes.append("TEMPORARY")
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        for column in table.columns:
            if isinstance(column.type, postgresql.ENUM) and column.type.name not in enums:
                enums.add(column.type.name)
                values = ",".join(f"'{value}'" for value in column.type.enums)
                await connection.execute(
                    f"CREATE TYPE pg_temp.{column.type.name} AS ENUM ({values})"
                )
        await connection.execute(str(CreateTable(table).compile(dialect=postgresql.dialect())))
        for index in table.indexes:
            if index.name != f"idx_{table.name}_created":
                await connection.execute(
                    str(CreateIndex(index).compile(dialect=postgresql.dialect()))
                )
        await connection.execute(seed(table.name, ROWS))
        await connection.execute(f"VACUUM ANALYZE {table.name}")


async def statements_for(connection):
    statements = {}
    for model in MODELS:
        name = model.__tablename__
        anchor = await connection.fetchrow(
            f"SELECT id,created_at FROM {name} ORDER BY created_at DESC,id DESC "
            "OFFSET 50000 LIMIT 1"
        )
        for size in (25, 100):
            for label, options in (
                ("first", BrowseOptions(page_size=size)),
                (
                    "cursor",
                    BrowseOptions(
                        page_size=size, after=anchor["id"], cursor_at=anchor["created_at"]
                    ),
                ),
                ("ascending", BrowseOptions(page_size=size, direction="asc")),
            ):
                statements[f"{name}_{label}_{size}"] = sql(
                    browse_query(model.__table__, options, [])
                )
    return statements


async def write_cost(connection, name):
    plans = []
    for _ in range(4):
        transaction = connection.transaction()
        await transaction.start()
        try:
            raw = await connection.fetchval(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + seed(name, 1000)
            )
            plans.append(json.loads(raw)[0])
        finally:
            await transaction.rollback()
    return plans


async def compare(connection):
    await create_tables(connection)
    statements = await statements_for(connection)
    report = {
        "rows_per_table": ROWS,
        "baseline": {},
        "candidate": {},
        "index_bytes": {},
        "writes": {},
    }
    for label, statement in statements.items():
        report["baseline"][label] = await measure(connection, statement)
    for model in MODELS:
        name = model.__tablename__
        report["writes"][name + "_before"] = await write_cost(connection, name)
        await connection.execute(
            f"CREATE INDEX idx_{name}_created ON {name}(created_at DESC,id DESC)"
        )
        report["index_bytes"][name] = await connection.fetchval(
            "SELECT pg_relation_size($1::regclass)", "pg_temp.idx_" + name + "_created"
        )
        report["writes"][name + "_after"] = await write_cost(connection, name)
    for label, statement in statements.items():
        report["candidate"][label] = await measure(connection, statement)
        assert report["candidate"][label]["results"] == report["baseline"][label]["results"]
    return report


async def main():
    url = os.environ["GIFTLY_PERF_DATABASE_URL"]
    if urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Use an explicitly disposable loopback server.")
    connection = await asyncpg.connect(url, timeout=10)
    try:
        version = int(await connection.fetchval("SHOW server_version_num"))
        if not 160000 <= version < 170000:
            raise ValueError("Use PostgreSQL16 for comparable measurements.")
        await connection.execute("SET statement_timeout='30s'")
        report = await compare(connection)
        await asyncio.to_thread(
            Path(".superpowers/audit-fix-08-plans.json").write_text,
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )
        print("Verified identical rows for 18 query shapes; wrote raw plans.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
