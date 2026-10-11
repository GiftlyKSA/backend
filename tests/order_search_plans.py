"""Measure order-prefix index read/write costs on disposable PostgreSQL temp tables.

Run with uv run --locked python -m tests.order_search_plans.
GIFTLY_PERF_DATABASE_URL must identify an explicitly disposable loopback database.
Synthetic results are query-plan evidence, not production capacity.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
from urllib.parse import urlparse

import asyncpg

QUERY = """SELECT * FROM search_orders WHERE customer_id=$1
AND left(lower(description),100) LIKE $2 ESCAPE '\\'
ORDER BY created_at DESC,id DESC LIMIT 26"""
EXPLAIN = """EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) EXECUTE order_search(
'cfcd2084-95d5-65ef-66e7-dff9f98764da','gift rare%')"""


async def main() -> None:
    """Use session-local synthetic data and report custom/generic/automatic plans."""
    url = os.environ["GIFTLY_PERF_DATABASE_URL"]
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a disposable loopback PostgreSQL database.")
    connection = await asyncpg.connect(url, statement_cache_size=0)
    results: dict[str, object] = {}
    try:
        await connection.execute("""
CREATE TEMP TABLE search_orders(id uuid PRIMARY KEY,customer_id uuid NOT NULL,
description text NOT NULL,created_at timestamptz NOT NULL);
INSERT INTO search_orders SELECT md5(n::text)::uuid,md5((n%2)::text)::uuid,
CASE WHEN n%10000=0 THEN 'Gift rare ' ELSE 'Other description ' END||n,
now()-n*interval '1 minute' FROM generate_series(1,100000)n;
CREATE INDEX search_owner ON search_orders(customer_id,created_at DESC,id DESC);
""")
        for indexed in (False, True):
            if indexed:
                await connection.execute("""
CREATE INDEX search_prefix ON search_orders(
customer_id,left(lower(description),100) text_pattern_ops)
""")
                results["index_bytes"] = await connection.fetchval(
                    "SELECT pg_relation_size('search_prefix')"
                )
            await connection.execute("ANALYZE search_orders")
            for mode in ("force_custom_plan", "force_generic_plan", "auto"):
                await connection.execute("SET plan_cache_mode=" + mode)
                await connection.execute("DEALLOCATE ALL")
                await connection.execute("PREPARE order_search(uuid,text) AS " + QUERY)
                plans = [json.loads(await connection.fetchval(EXPLAIN))[0] for _ in range(7)]
                results[f"{indexed}_{mode}"] = {
                    "median_ms": statistics.median(p["Execution Time"] for p in plans),
                    "plan": plans[-1],
                }
            writes = []
            for _ in range(7):
                transaction = connection.transaction()
                await transaction.start()
                try:
                    plan = json.loads(
                        await connection.fetchval("""
EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) INSERT INTO search_orders SELECT
md5((100000+n)::text)::uuid,md5((n%2)::text)::uuid,
'Other description '||n,now() FROM generate_series(1,1000)n
""")
                    )[0]
                    writes.append(plan["Execution Time"])
                finally:
                    await transaction.rollback()
            results[f"{indexed}_write1000_median_ms"] = statistics.median(writes)
        print(json.dumps(results, indent=2))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
