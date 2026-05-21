#!/usr/bin/env python3
from __future__ import annotations

"""Grow LiteLLM_SpendLogs to a target size and benchmark a full table read."""

"""
Usage:
1. fill DB with 1gb of data: `python scripts/benchmark_spendlogs_table.py --truncate-first --target-size-gb 1 --skip-benchmark`
2. benchmark monthly view table: `python scripts/benchmark_spendlogs_table.py --skip-load --benchmark-target monthly-view`
3. run migration with `DB_HOST=localhost DB_PORT=5432 DB_USERNAME=litellm DB_PASSWORD=litellm LiteLLM_DB_NAME=litellm bash scripts/migrate-litellm-database.sh`
4. benchmark migrated monthly view table: `python scripts/benchmark_spendlogs_table.py --skip-load --benchmark-target monthly-view`
5. clean up: `python scripts/benchmark_spendlogs_table.py --truncate-first --skip-load`

"""


import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psycopg2.extensions import connection as PGConnection
else:
    PGConnection = Any


DEFAULT_DSN = os.environ.get(
    "LITELLM_BENCHMARK_DSN",
    "postgresql://litellm:litellm@localhost:5432/litellm",
)
TABLE_NAME = 'public."LiteLLM_SpendLogs"'


@dataclass(frozen=True)
class BenchmarkConfig:
    dsn: str
    target_bytes: int
    batch_rows: int
    payload_bytes: int
    fetch_size: int
    skip_load: bool
    skip_benchmark: bool
    truncate_first: bool
    analyze: bool
    benchmark_target: str


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Fill LiteLLM_SpendLogs with fake rows until it reaches a target size, "
            "then stream SELECT * and report timing."
        )
    )
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="Postgres DSN to benchmark.")
    parser.add_argument(
        "--target-size-gb",
        type=float,
        default=1.0,
        help="Target table size in GiB based on pg_total_relation_size(). Default: 1.0",
    )
    parser.add_argument(
        "--batch-rows",
        type=int,
        default=5000,
        help="Rows inserted per batch. Default: 5000",
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=2048,
        help=(
            "Approximate payload bytes stored in JSON/text-heavy columns per row. "
            "Larger values reach the target size with fewer rows. Default: 2048"
        ),
    )
    parser.add_argument(
        "--fetch-size",
        type=int,
        default=2000,
        help="Rows fetched per round trip during SELECT * benchmark. Default: 2000",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Do not insert fake data; only benchmark the current table contents.",
    )
    parser.add_argument(
        "--skip-benchmark",
        action="store_true",
        help="Load data but do not run SELECT * afterwards.",
    )
    parser.add_argument(
        "--truncate-first",
        action="store_true",
        help="Delete previously generated benchmark rows (`api_key = 'fake-api-key-benchmark'`) before loading.",
    )
    parser.add_argument(
        "--no-analyze",
        action="store_true",
        help="Skip ANALYZE after loading.",
    )
    parser.add_argument(
        "--benchmark-target",
        choices=("table", "monthly-view"),
        default="table",
        help=(
            "Which query path to benchmark: `table` streams SELECT * from "
            "LiteLLM_SpendLogs, while `monthly-view` benchmarks "
            "SELECT SUM(spend) FROM MonthlyGlobalSpend. Default: table"
        ),
    )
    args = parser.parse_args()

    if args.batch_rows <= 0:
        parser.error("--batch-rows must be > 0")
    if args.payload_bytes < 256:
        parser.error("--payload-bytes must be >= 256")
    if args.fetch_size <= 0:
        parser.error("--fetch-size must be > 0")
    if args.target_size_gb <= 0:
        parser.error("--target-size-gb must be > 0")

    return BenchmarkConfig(
        dsn=args.dsn,
        target_bytes=int(args.target_size_gb * (1024**3)),
        batch_rows=args.batch_rows,
        payload_bytes=args.payload_bytes,
        fetch_size=args.fetch_size,
        skip_load=args.skip_load,
        skip_benchmark=args.skip_benchmark,
        truncate_first=args.truncate_first,
        analyze=not args.no_analyze,
        benchmark_target=args.benchmark_target,
    )


def connect(dsn: str, autocommit: bool = True) -> PGConnection:
    try:
        import psycopg2
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "psycopg2 is not installed. Run this script from the project environment, "
            "for example: `uv run python scripts/benchmark_spendlogs_table.py ...`"
        ) from exc

    conn = psycopg2.connect(dsn)
    conn.autocommit = autocommit
    return conn


def ensure_table_exists(conn: PGConnection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (TABLE_NAME,))
        if cur.fetchone()[0] is None:
            raise SystemExit(
                f"{TABLE_NAME} does not exist. Start LiteLLM locally and let it run its migrations first."
            )


def get_table_stats(conn: PGConnection) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              pg_total_relation_size('{TABLE_NAME}'),
              COUNT(*)
            FROM {TABLE_NAME}
            """
        )
        size_bytes, row_count = cur.fetchone()
    return int(size_bytes), int(row_count)


def delete_fake_rows(conn: PGConnection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {TABLE_NAME} WHERE api_key = 'fake-api-key-benchmark'"
        )


def reclaim_table_storage(dsn: str) -> None:
    conn = connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"VACUUM FULL ANALYZE {TABLE_NAME}")
    finally:
        conn.close()


def load_fake_data(conn: PGConnection, config: BenchmarkConfig) -> None:
    insert_sql = f"""
        INSERT INTO {TABLE_NAME} (
          request_id,
          call_type,
          api_key,
          spend,
          total_tokens,
          prompt_tokens,
          completion_tokens,
          "startTime",
          "endTime",
          "completionStartTime",
          model,
          model_id,
          model_group,
          custom_llm_provider,
          api_base,
          "user",
          metadata,
          cache_hit,
          cache_key,
          request_tags,
          team_id,
          end_user,
          requester_ip_address,
          messages,
          response,
          proxy_server_request,
          session_id,
          status,
          mcp_namespaced_tool_name,
          organization_id,
          agent_id,
          request_duration_ms
        )
        SELECT
          'bench-' || txid_current() || '-' || gs::text || '-' || substr(md5(random()::text), 1, 16),
          'chat.completion',
          'fake-api-key-benchmark',
          round((random() * 100)::numeric, 4)::double precision,
          800 + (random() * 4000)::int,
          500 + (random() * 2000)::int,
          300 + (random() * 2000)::int,
          NOW() - ((random() * 29)::int || ' days')::interval,
          NOW() - ((random() * 29)::int || ' days')::interval + ((50 + random() * 5000)::int || ' milliseconds')::interval,
          NOW() - ((random() * 29)::int || ' days')::interval + ((10 + random() * 300)::int || ' milliseconds')::interval,
          'gemini-2.5-pro',
          'gemini-2.5-pro',
          'bench-group',
          'vertex_ai',
          'https://example.googleapis.com',
          'bench-user-' || (gs %% 10000)::text,
          jsonb_build_object(
            'benchmark', true,
            'payload', repeat('m', %s),
            'batch_row', gs
          ),
          CASE WHEN gs %% 10 = 0 THEN 'true' ELSE 'false' END,
          'cache-' || gs::text,
          jsonb_build_array('benchmark', 'synthetic'),
          'team-' || (gs %% 100)::text,
          'end-user-' || (gs %% 50000)::text,
          '127.0.0.1',
          jsonb_build_object(
            'role', 'user',
            'content', repeat('u', GREATEST(32, %s / 2))
          ),
          jsonb_build_object(
            'id', 'resp-' || gs::text,
            'content', repeat('r', GREATEST(32, %s / 2))
          ),
          jsonb_build_object(
            'method', 'POST',
            'path', '/chat/completions',
            'body', repeat('b', GREATEST(32, %s / 2))
          ),
          'session-' || (gs %% 10000)::text,
          'success',
          NULL,
          'org-' || (gs %% 25)::text,
          NULL,
          50 + (random() * 5000)::int
        FROM generate_series(1, %s) AS gs
    """

    while True:
        current_size, row_count = get_table_stats(conn)
        pct = current_size / config.target_bytes * 100
        print(
            f"[load] size={format_bytes(current_size)} rows={row_count:,} "
            f"target={format_bytes(config.target_bytes)} ({pct:.1f}%)"
        )
        if current_size >= config.target_bytes:
            break

        start = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(
                insert_sql,
                (
                    config.payload_bytes,
                    config.payload_bytes,
                    config.payload_bytes,
                    config.payload_bytes,
                    config.batch_rows,
                ),
            )
        elapsed = time.perf_counter() - start
        print(
            f"[load] inserted {config.batch_rows:,} rows in {elapsed:.2f}s "
            f"({config.batch_rows / max(elapsed, 1e-9):,.0f} rows/s)"
        )

    if config.analyze:
        print("[load] running ANALYZE on LiteLLM_SpendLogs")
        with conn.cursor() as cur:
            cur.execute(f"ANALYZE {TABLE_NAME}")


def benchmark_select_star(dsn: str, fetch_size: int) -> None:
    conn = connect(dsn, autocommit=False)
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) SELECT * FROM {TABLE_NAME}"

    try:
        with conn.cursor() as cur:
            cur.execute(explain_sql)
            explain_lines = [row[0] for row in cur.fetchall()]
        print("[benchmark] server-side EXPLAIN ANALYZE:")
        for line in explain_lines:
            print(f"  {line}")
        conn.rollback()

        total_rows = 0
        start = time.perf_counter()
        with conn.cursor(name="spendlogs_benchmark_cursor") as cur:
            cur.itersize = fetch_size
            cur.execute(f"SELECT * FROM {TABLE_NAME}")
            while True:
                rows = cur.fetchmany(fetch_size)
                if not rows:
                    break
                total_rows += len(rows)
        elapsed = time.perf_counter() - start
        print(
            f"[benchmark] streamed SELECT * rows={total_rows:,} elapsed={elapsed:.2f}s "
            f"rows_per_sec={total_rows / max(elapsed, 1e-9):,.0f}"
        )
        conn.rollback()
    finally:
        conn.close()


def benchmark_monthly_view(dsn: str) -> None:
    conn = connect(dsn, autocommit=False)
    explain_sql = (
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
        'SELECT SUM(spend) AS total_spend FROM public."MonthlyGlobalSpend"'
    )
    query_sql = 'SELECT SUM(spend) AS total_spend FROM public."MonthlyGlobalSpend"'

    try:
        with conn.cursor() as cur:
            cur.execute(explain_sql)
            explain_lines = [row[0] for row in cur.fetchall()]
        print("[benchmark] server-side EXPLAIN ANALYZE:")
        for line in explain_lines:
            print(f"  {line}")
        conn.rollback()

        start = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(query_sql)
            total_spend = cur.fetchone()[0]
        elapsed = time.perf_counter() - start
        print(
            f"[benchmark] monthly view query elapsed={elapsed:.6f}s "
            f"total_spend={total_spend}"
        )
        conn.rollback()
    finally:
        conn.close()


def format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"

    units = ["KiB", "MiB", "GiB", "TiB"]
    value = float(num_bytes)
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
    return f"{num_bytes} B"


def main() -> int:
    config = parse_args()
    print(f"[setup] connecting to {config.dsn}")
    conn = connect(config.dsn)
    try:
        ensure_table_exists(conn)

        if config.truncate_first:
            print(f"[setup] deleting benchmark rows from {TABLE_NAME}")
            delete_fake_rows(conn)
            print(f"[setup] reclaiming storage for {TABLE_NAME}")
            reclaim_table_storage(config.dsn)

        if not config.skip_load:
            load_fake_data(conn, config)

        size_bytes, row_count = get_table_stats(conn)
        print(
            f"[summary] current size={format_bytes(size_bytes)} rows={row_count:,} "
            f"target={format_bytes(config.target_bytes)}"
        )

        if not config.skip_benchmark:
            if config.benchmark_target == "table":
                benchmark_select_star(config.dsn, config.fetch_size)
            else:
                benchmark_monthly_view(config.dsn)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
