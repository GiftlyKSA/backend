"""Reproduce bounded AP-P07/08/09 plans on disposable PostgreSQL 16 temp tables.

Run with uv run --locked python -m tests.financial_query_plans.
Set GIFTLY_PERF_DATABASE_URL to an explicitly disposable loopback PostgreSQL URL.
Only session-local tables/types are created; no application data is read or changed.
Output includes every raw EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) plan and index size.
Synthetic histories model two heavy owners among 100,000 rows, not production capacity.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
from app.models import Occasion, Order, PaymentIntent, Rating, Transaction
from app.models.enums import OrderStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.planning_repository import PlanningRepository
from app.repositories.rating_repository import RatingRepository
from app.repositories.wallet_repository import WalletRepository
from sqlalchemy import ForeignKeyConstraint, MetaData
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

OWNER = UUID(int=1)
START = datetime(2020, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)
ROWS = 100_000
DEPLOYED_INDEXES = {
    "idx_orders_customer_calendar",
    "idx_orders_courier_calendar",
    "idx_payment_intents_topup_recovery",
    "idx_payment_intents_open_topup_recovery",
    "idx_payment_intents_order_recovery",
}


def sql(query: object) -> str:
    return str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


async def queries() -> dict[str, str]:
    session = AsyncMock()
    session.scalar.return_value = None
    session.scalars.return_value = []
    session.execute.return_value = [(None, *(Decimal(0) for _ in range(6)), START)]
    wallet = WalletRepository(session)
    await wallet.statement(OWNER, start=START, end=END, limit=26, cursor=None)
    result = {"wallet_range": sql(session.execute.call_args.args[0])}
    payments = PaymentRepository(session)
    await payments.get_topup_for_actor(OWNER)
    result["topup_history"] = sql(session.scalar.call_args.args[0])
    await payments.get_topup_for_actor(OWNER, open_only=True)
    result["topup_open"] = sql(session.scalar.call_args.args[0])
    await payments.get_latest_intent_for_order(order_id=OWNER, user_id=OWNER)
    result["order_recovery"] = sql(session.scalar.call_args.args[0])
    orders = OrderRepository(session)
    for role in ("customer", "courier"):
        for label, lower, upper, status in (
            ("narrow", date(2020, 5, 1), date(2020, 5, 1), None),
            ("week", date(2020, 5, 1), date(2020, 5, 7), None),
            ("month", date(2020, 5, 1), date(2020, 5, 31), None),
            ("broad", date(2020, 1, 1), date(2021, 6, 1), None),
            ("status", date(2020, 5, 1), date(2020, 5, 7), OrderStatus.DELIVERED),
        ):
            await getattr(orders, f"list_for_{role}")(
                OWNER, status=status, limit=26, before_id=None, from_date=lower, to_date=upper
            )
            result[f"calendar_{role}_{label}"] = sql(session.scalars.call_args.args[0])
    await PlanningRepository(session).list_occasions_for_actor(
        OWNER, limit=26, from_date=date(2020, 5, 1), to_date=date(2020, 5, 7)
    )
    result["occasions_range"] = sql(session.scalars.call_args.args[0])
    session.execute.return_value = AsyncMock()
    session.execute.return_value.first = lambda: (Decimal(0), 0)
    await RatingRepository(session).summary_for_user(OWNER)
    result["rating_summary"] = sql(session.execute.call_args.args[0])
    result["rating_count_star"] = result["rating_summary"].replace("count(ratings.id)", "count(*)")
    return result


async def tables(connection: asyncpg.Connection) -> None:
    await connection.execute("CREATE TEMP TABLE perf_session_marker (id integer)")
    metadata = MetaData()
    types: set[str] = set()
    for model in (Transaction, PaymentIntent, Order, Occasion, Rating):
        table = model.__table__.to_metadata(metadata)
        table._prefixes.append("TEMPORARY")
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        for column in table.columns:
            if isinstance(column.type, postgresql.ENUM) and column.type.name not in types:
                types.add(column.type.name)
                literals = ",".join(f"'{value}'" for value in column.type.enums)
                await connection.execute(
                    f"CREATE TYPE pg_temp.{column.type.name} AS ENUM ({literals})"
                )
        await connection.execute(
            str(
                CreateTable(table, include_foreign_key_constraints=[]).compile(
                    dialect=postgresql.dialect()
                )
            )
        )
        for index in table.indexes:
            if index.name in DEPLOYED_INDEXES:
                continue
            await connection.execute(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
    await connection.execute("""
        INSERT INTO transactions
            (wallet_id,amount,type,status,correlation_id,balance_after,created_at)
        SELECT lpad(to_hex(CASE WHEN i % 10 < 9 THEN 1 ELSE 2 END),32,'0')::uuid,
               CASE WHEN i%2=0 THEN 12.34 ELSE -12.34 END, 'TOPUP',
               (CASE WHEN i%10=0 THEN 'PENDING' WHEN i%11=0 THEN 'REVERSED'
                     ELSE 'SETTLED' END)::transaction_status,
               gen_random_uuid(),0,'2020-01-01'::timestamptz+i*interval '1 minute'
        FROM generate_series(1,100000) AS i
    """)

    await connection.execute("""
        INSERT INTO payment_intents (user_id,purpose,amount,status,checkout_provider,order_id,
            checkout_state,reference_invoice_id,expires_at,created_at)
        SELECT lpad(to_hex(CASE WHEN i%2=0 THEN 1 ELSE 2 END),32,'0')::uuid,
            (CASE WHEN i%4<2 THEN 'WALLET_TOPUP' ELSE 'ORDER_INVOICE' END)::payment_purpose,
            12.34,(CASE WHEN i IN (99997,99998,99999,100000)
                THEN 'NEW' ELSE 'PAID' END)::payment_intent_status,
            'DHAMEN',CASE WHEN i%4>=2
                THEN lpad(to_hex(CASE WHEN i%2=0 THEN 1 ELSE 2 END),32,'0')::uuid END,
            CASE WHEN i IN (100,101,102,103) THEN 'REVIEW' ELSE 'CLOSED' END,
            CASE WHEN i%4>=2 THEN gen_random_uuid() END,
            '2028-01-01'::timestamptz,'2020-01-01'::timestamptz+i*interval '1 minute'
        FROM generate_series(1,100000) AS i
    """)
    await connection.execute("""
        INSERT INTO orders (customer_id,courier_id,delivery_city_id,delivery_date,status,
            description,created_at)
        SELECT lpad(to_hex(CASE WHEN i%2=0 THEN 1 ELSE 2 END),32,'0')::uuid,
            lpad(to_hex(CASE WHEN i%2=0 THEN 2 ELSE 1 END),32,'0')::uuid,gen_random_uuid(),
            '2020-01-01'::date+(i/300)::int+(i%180)::int,
            (CASE WHEN i%4<2 THEN 'DELIVERED' ELSE 'CANCELLED' END)::order_status,
            repeat('synthetic gift ',20),'2020-01-01'::timestamptz+(i/300)*interval '1 day'
        FROM generate_series(1,100000) AS i
    """)
    await connection.execute("""
        INSERT INTO occasions (user_id,title,occasion_date,reminder_days_before)
        SELECT lpad(to_hex(CASE WHEN i%2=0 THEN 1 ELSE 2 END),32,'0')::uuid,
            'Synthetic occasion','2020-01-01'::date+(i/300)::int,7
        FROM generate_series(1,100000) AS i
    """)
    await connection.execute("""
        INSERT INTO ratings (order_id,rater_id,rated_user_id,score,comment)
        SELECT id,customer_id,courier_id,1+(row_number() OVER ()%5),repeat('synthetic ',30)
        FROM orders
    """)
    for table in ("transactions", "payment_intents", "orders", "occasions", "ratings"):
        await connection.execute(f"VACUUM ANALYZE pg_temp.{table}")


CANDIDATES = {
    "payment_open": "CREATE INDEX perf_payment_open ON payment_intents "
    "(user_id,(checkout_state='REVIEW') DESC,(status='NEW') DESC,created_at DESC,id DESC) "
    "WHERE purpose='WALLET_TOPUP' AND checkout_provider!='SIMULATED' "
    "AND (status='NEW' OR checkout_state='REVIEW')",
    "calendar_customer_status": "CREATE INDEX perf_calendar_customer_status ON orders "
    "(customer_id,status,delivery_date,created_at DESC,id DESC)",
    "calendar_courier_status": "CREATE INDEX perf_calendar_courier_status ON orders "
    "(courier_id,status,delivery_date,created_at DESC,id DESC) WHERE courier_id IS NOT NULL",
    "rating_covering": "CREATE INDEX perf_rating_covering ON ratings "
    "(rated_user_id) INCLUDE (score,order_id,rater_id,id)",
    "wallet_covering": "CREATE INDEX perf_wallet_covering ON transactions "
    "(wallet_id,created_at DESC,id DESC) INCLUDE (status,amount)",
    "calendar_customer": "CREATE INDEX perf_calendar_customer ON orders "
    "(customer_id,delivery_date,created_at DESC,id DESC)",
    "calendar_courier": "CREATE INDEX perf_calendar_courier ON orders "
    "(courier_id,delivery_date,created_at DESC,id DESC) WHERE courier_id IS NOT NULL",
    "occasion_keyset": "CREATE INDEX perf_occasion_keyset ON occasions (user_id,occasion_date,id)",
    "payment_user": "CREATE INDEX perf_payment_user ON payment_intents "
    "(user_id,(checkout_state='REVIEW') DESC,(status='NEW') DESC,created_at DESC,id DESC) "
    "WHERE purpose='WALLET_TOPUP' AND checkout_provider!='SIMULATED'",
    "payment_order": "CREATE INDEX perf_payment_order ON payment_intents "
    "(order_id,user_id,(checkout_state='REVIEW') DESC,(status='NEW') DESC,"
    "created_at DESC,id DESC) WHERE order_id IS NOT NULL",
}


async def measure(connection: asyncpg.Connection, query: str) -> dict[str, object]:
    plans = []
    for _ in range(4):
        raw = await connection.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query)
        plans.append(json.loads(raw)[0])
    rows = await connection.fetch(query)
    if "statement_timestamp()" in query:
        rows = [tuple(row)[:-1] for row in rows]
    return {"runs": plans, "results": [[str(value) for value in row] for row in rows]}


async def prepared_plans(connection: asyncpg.Connection) -> dict[str, object]:
    from sqlalchemy import literal
    from sqlalchemy.dialects.postgresql.asyncpg import dialect

    session = AsyncMock()
    session.scalar.return_value = None
    repo = PaymentRepository(session)
    result = {}
    for label in ("history", "open", "order"):
        if label == "order":
            await repo.get_latest_intent_for_order(order_id=OWNER, user_id=OWNER)
        else:
            await repo.get_topup_for_actor(OWNER, open_only=label == "open")
        compiled = session.scalar.call_args.args[0].compile(dialect=dialect())
        arguments = ",".join(
            str(
                literal(compiled.params[name], type_=compiled.binds[name].type).compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                )
            )
            for name in compiled.positiontup
        )
        statement = "perf_prepared_" + label
        await connection.execute(f"PREPARE {statement} AS {compiled}")
        for mode in ("force_custom_plan", "force_generic_plan", "auto"):
            await connection.execute("SET plan_cache_mode = " + mode)
            for _ in range(7 if mode == "auto" else 1):
                raw = await connection.fetchval(
                    f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE {statement}({arguments})"
                )
            result[label + "_" + mode] = json.loads(raw)[0]
        await connection.execute("DEALLOCATE " + statement)
    await connection.execute("RESET plan_cache_mode")
    return result


async def writes(connection: asyncpg.Connection) -> dict[str, object]:
    statements = {
        "orders_insert_1000": """
            INSERT INTO orders (customer_id,courier_id,delivery_city_id,delivery_date,
                status,description,created_at)
            SELECT customer_id,courier_id,delivery_city_id,delivery_date,status,
                description,created_at FROM orders LIMIT 1000
        """,
        "payments_insert_1000": """
            INSERT INTO payment_intents (user_id,purpose,amount,status,checkout_provider,
                order_id,checkout_state,reference_invoice_id,expires_at,created_at)
            SELECT user_id,purpose,amount,'PAID',checkout_provider,order_id,'CLOSED',
                reference_invoice_id,expires_at,created_at FROM payment_intents LIMIT 1000
        """,
        "payment_close_active": "UPDATE payment_intents SET status='PAID', "
        "checkout_state='CLOSED' WHERE status='NEW'",
    }
    result = {}
    for label, query in statements.items():
        runs = []
        for _ in range(4):
            transaction = connection.transaction()
            await transaction.start()
            try:
                raw = await connection.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query)
                runs.append(json.loads(raw)[0])
            finally:
                await transaction.rollback()
        result[label] = runs
    return result


async def small_sample(
    connection: asyncpg.Connection, statements: dict[str, str]
) -> dict[str, object]:
    sample = {"baseline": {}, "candidate": {}, "rows": {}}
    for label in CANDIDATES:
        await connection.execute("DROP INDEX IF EXISTS pg_temp.perf_" + label)
    for table in ("payment_intents", "orders"):
        preserve = (
            " OR status='NEW' OR checkout_state='REVIEW'" if table == "payment_intents" else ""
        )
        await connection.execute(
            f"DELETE FROM pg_temp.{table} WHERE NOT (left(md5(id::text),2)='00'{preserve})"
        )
        await connection.execute(f"VACUUM FULL ANALYZE pg_temp.{table}")
        sample["rows"][table] = await connection.fetchval(f"SELECT count(*) FROM pg_temp.{table}")
    selected = {
        key: value
        for key, value in statements.items()
        if key.startswith(("topup", "order_recovery", "calendar"))
    }
    for label, query in selected.items():
        sample["baseline"][label] = await measure(connection, query)
    for label in (
        "payment_user",
        "payment_open",
        "payment_order",
        "calendar_customer",
        "calendar_courier",
    ):
        await connection.execute(CANDIDATES[label])
    for label, query in selected.items():
        sample["candidate"][label] = await measure(connection, query)
        assert sample["baseline"][label]["results"] == sample["candidate"][label]["results"]
    return sample


async def main() -> None:
    url = os.environ["GIFTLY_PERF_DATABASE_URL"]
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Only an explicitly disposable loopback PostgreSQL server is supported.")
    connection = await asyncpg.connect(url, timeout=10)
    try:
        version = await connection.fetchval("SHOW server_version_num")
        if not 160000 <= int(version) < 170000:
            raise ValueError("PostgreSQL 16 is required for comparable plans.")
        await connection.execute("SET statement_timeout = '30s'")
        await tables(connection)
        statements = await queries()
        report = {
            "postgres_version": version,
            "rows_per_table": ROWS,
            "baseline": {},
            "candidate": {},
            "index_bytes": {},
        }
        for label, query in statements.items():
            report["baseline"][label] = await measure(connection, query)
        report["baseline_writes"] = await writes(connection)
        for label, ddl in CANDIDATES.items():
            await connection.execute(ddl)
            index_name = "perf_" + label
            report["index_bytes"][label] = await connection.fetchval(
                "SELECT pg_relation_size($1::regclass)", "pg_temp." + index_name
            )
        for label, query in statements.items():
            report["candidate"][label] = await measure(connection, query)
            assert report["baseline"][label]["results"] == report["candidate"][label]["results"]
        report["candidate_writes"] = await writes(connection)
        for label in (
            "calendar_customer_status",
            "calendar_courier_status",
            "rating_covering",
            "wallet_covering",
            "occasion_keyset",
        ):
            await connection.execute("DROP INDEX pg_temp.perf_" + label)
        report["approved_writes"] = await writes(connection)
        report["prepared_plans"] = await prepared_plans(connection)
        report["small_sample"] = await small_sample(connection, statements)
        destination = Path(
            os.environ.get(
                "GIFTLY_PERF_REPORT",
                ".superpowers/sdd/remaining-performance/financial-plans.json",
            )
        )
        await asyncio.to_thread(
            destination.write_text, json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Wrote {destination}: same results for {len(statements)} query shapes.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
