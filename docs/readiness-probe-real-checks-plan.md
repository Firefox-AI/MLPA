# Readiness probe runs real dependency checks (AIPLAT-916)

## Summary

`/health/readiness` currently returns HTTP 200 for every request, regardless of
what its body reports. Kubernetes routes traffic on the **status code**, so a
pod whose Postgres pool is down or whose `app_attest` schema is behind the code
it ships still stays in rotation and serves real users 500s. This plan makes the
readiness probe answer the question it exists to answer — *"can this pod serve a
request right now?"* — by running a live query against both Postgres pools,
asserting the `app_attest` migration heads match the code, and surfacing the
result as the HTTP status code. Liveness stays dumb so the change can never
cause a crash-loop.

## Current behavior

`src/mlpa/core/routers/health/health.py`:

```python
@router.get("/readiness", tags=["Health"])
async def readiness_probe():
    # todo add check to PG and LiteLLM status here
    pg_status = litellm_pg.check_status()
    app_attest_pg_status = app_attest_pg.check_status()
    ...
    return { "status": "connected", "pg_server_dbs": {...}, "litellm": litellm_status }
```

Two gaps:

1. **The status code never changes.** The handler always returns the dict, so
   FastAPI always answers 200. Even when `check_status()` reports `offline`, the
   probe passes and the pod keeps taking traffic.
2. **`check_status()` doesn't touch the database.** It reads the private
   `asyncpg.Pool._closed` flag in `PGService.check_status()`. A pool can be "open" while
   the server behind it is unreachable, statement-timeouting, or schema-stale.
   The flag answers "did we call `.close()`?", not "can I run a query?".

A third, related gap motivates the migration check specifically: deploys apply
schema migrations as a **separate presync job**
(`scripts/migrate-app-attest-database.sh`), the same class of decoupled step
that AIPLAT-745 showed can fail without stopping downstream pods. A new pod can
boot with code expecting a schema revision the database hasn't reached yet. The
database is up and `SELECT 1` passes — it's the *schema* that's behind — so only
an explicit revision assertion catches it.

## Goals

- Readiness fails (non-2xx) when any serving dependency is unhealthy, so
  Kubernetes pulls the pod from the Service endpoints.
- Both Postgres pools answered a real query within the probe window.
- The `app_attest` database is at the Alembic head(s) the running code ships.
- LiteLLM upstream remains a hard dependency (decision below).
- Liveness stays a constant `{"status": "alive"}` — readiness failures must not
  restart pods.

## Non-goals

- Changing how migrations are applied or how the presync job reports failure
  (that is AIPLAT-745's surface, in `dataservices-infra`). This plan reduces the
  blast radius of that scenario; it doesn't replace the job.
- Adding a migration check for the `litellm` database. That schema is owned and
  versioned by LiteLLM, not Alembic — MLPA only asserts the head it manages
  itself, on `app_attest`.

## Design

The probe runs three checks and returns 200 only if all pass; otherwise 503 with
a body naming the failed check. The three: a live query on the `litellm` pool,
revision reads on the `app_attest` pool (which double as that pool's liveness
check), and a LiteLLM HTTP readiness check.

### 1. Live query per pool

Replace the `_closed`-flag read with a real round-trip, each wrapped in an
explicit timeout:

```python
async with asyncio.timeout(READINESS_CHECK_TIMEOUT_S):  # e.g. 2s
    await pool.fetchval("SELECT 1")
```

The outer `asyncio.timeout` is the **sole** bound, not belt-and-suspenders. The
pool is created with no `statement_timeout` and no `command_timeout`
(`pg_service.py:25-30`) — nothing server-side or client-side bounds a hung query
or `pool.acquire()` establishing a fresh connection. So against a blackholed
host, a connection refused that retries, or an exhausted pool
(`PG_POOL_MAX_SIZE=10`), the await can hang indefinitely with nothing to release
it. The explicit timeout is the only thing that guarantees the probe returns
within a bounded window. Any raised exception or timeout maps that database to
`offline`.

**One live query per pool, not two on `app_attest`.** The `litellm` pool gets a
dedicated `SELECT 1` (it has no revision read). The `app_attest` pool's liveness
is proven by the revision read in §2 — a `SELECT version_num` that returns
already establishes the pool answers a query, so a separate `SELECT 1` on
`app_attest` is redundant. Derive `app_attest: connected` from
`current_revisions()` not raising; only `litellm` needs an explicit `ping()`.
This saves one pool connection per probe and removes a check from the matrix.

### 2. `app_attest` migration head assertion

- **Expected heads** are resolved from the migration files the pod ships, via
  Alembic's `ScriptDirectory`, and **memoized** (computed once, on first probe,
  then cached for the process). Adding a migration updates the expected set
  automatically — nothing to bump by hand.

  Build the `ScriptDirectory` from the `script_location` directly rather than
  parsing `alembic.ini`. The path is anchored to the package location, not the
  process CWD, so it survives whatever working directory the entrypoint runs in:

  ```python
  from pathlib import Path
  import mlpa
  from alembic.config import Config
  from alembic.script import ScriptDirectory

  # /app/src/mlpa/__init__.py -> parents[2] == repo root (/app) in the image
  _ALEMBIC_DIR = Path(mlpa.__file__).resolve().parents[2] / "alembic"

  def resolve_expected_heads() -> set[str]:
      cfg = Config()
      cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
      return set(ScriptDirectory.from_config(cfg).get_heads())
  ```

  Today this resolves to `{"919c4d382c42"}`.

  **If the heads can't be resolved, fail readiness — do not crash the process.**
  `sys.exit` here would crash-loop the entire deployment over what is almost
  always a packaging/path bug, turning a safety check into a self-inflicted
  outage and contradicting the "never crash-loop" principle below. Returning
  not-ready instead keeps pods alive and debuggable, drains them via the same
  mechanism as any other failed check, and is recoverable by revert. Log the
  reason loudly so it's not silent.

  **Multiple heads:** use `get_heads()`, not `get_current_head()`. Linear
  history is the only case today, but Alembic can validly represent branched
  histories with more than one head. Treating multiple heads as an error would
  make this probe hostile to a future valid migration graph. The readiness
  assertion should compare sets so a future branch-aware migration state is
  handled without redesigning the probe.

  **Packaging assumption (document, don't silently depend on):** head-from-files
  works because the image is built `COPY . .` + `uv pip install --editable .`, so
  the top-level `alembic/` tree is present on disk. `alembic/` is *not* under
  `src/` and would not be included by a non-editable wheel build. If packaging
  ever moves to a built wheel, the expected heads must be sourced another way
  (e.g. injected by the migration job or written into the package at build time).
  Until then this is a sound, zero-maintenance source of truth.

- **Current revisions** are read from the database at probe time:

  ```python
  rows = await app_attest_pg.pool.fetch("SELECT version_num FROM alembic_version")
  current = {row["version_num"] for row in rows}
  ```

  Two distinct failure modes, two distinct body shapes:
  - A missing `alembic_version` table (never migrated) raises
    `asyncpg.UndefinedTableError` → `current = set()` → not ready, `migration`
    shows `current: []`.
  - A connection/timeout error (pool down, host unreachable) propagates out of
    `current_revisions()` → caught by the handler as `app_attest` `offline`.
    This is the app_attest liveness signal folded in per §1 — distinct from the
    `current: []` case above.

- **Assertion:** `current == expected_heads`. Mismatch → the pod is running
  against a schema state other than the one its code ships with → readiness
  fails. This catches both stale databases (`current` behind `expected`) and
  accidental rollbacks to older code against a newer migration state (`current`
  has an unknown head).

### 3. LiteLLM upstream — hard dependency

Keep LiteLLM as a hard dependency: a pod that can't reach LiteLLM can't serve
completions or search, so it should leave rotation. Two details the current code
gets wrong and this plan corrects:

- **Check the status code.** Today the handler does `litellm_status = data` from
  `response.json()` and never inspects `response.status_code` — so a LiteLLM that
  returns 503-with-a-body is recorded as healthy. The check must require
  `response.status_code == 200`, and wrap transport errors/timeouts as a failed
  check rather than letting them escape as an unstructured 500.

- **Depend on LiteLLM readiness, not liveliness.** `/health/liveliness` only
  proves the LiteLLM container is alive; LiteLLM documents it for container
  liveness probes and it returns plain text (`"I'm alive!"`), not the JSON body
  this handler currently parses. MLPA sends requests through LiteLLM with virtual
  keys and relies on LiteLLM's serving path and backing database for budgets, key
  lookup, usage accounting, and search/completion routing. A live-but-not-ready
  LiteLLM is therefore not enough for MLPA to serve successfully.

  Keep using the existing `LITELLM_READINESS_URL`. Treat LiteLLM as ready only
  when the HTTP status is 200 and the body is compatible with LiteLLM readiness:
  `db == "connected"` plus a healthy top-level status. LiteLLM versions have used
  `"healthy"` and `"connected"` in examples/tests, so the check should be strict
  about `db` and status code, but tolerant of the known healthy status strings
  rather than hardcoding only one.

> Escape hatch: the LiteLLM check is independent of the Postgres/migration
> checks, so if coupling to LiteLLM readiness proves too aggressive in production
> it can be softened to report-only without touching the database logic. Do not
> swap to `/health/liveliness` as a drop-in replacement unless the response
> parser and semantics are intentionally changed too.

### 4. Run the checks concurrently

The `litellm` `SELECT 1`, the `app_attest` revision reads, and the LiteLLM call
are independent.
Run them under `asyncio.gather(..., return_exceptions=True)` — `return_exceptions`
so a single failing check doesn't cancel the siblings, letting the handler report
each one's status in the body. Worst-case probe latency becomes
`max(per-check timeouts)` (~2s) instead of their sum (~8s).

Note this is latency reduction, not a correctness requirement: even a single
check at ~2s exceeds Kubernetes' default `readinessProbe.timeoutSeconds` of 1, so
the probe timing must be tuned in the chart regardless (see below). `gather`
keeps that required `timeoutSeconds` small and avoids the probe holding multiple
pool connections for longer than necessary.

### Response contract

**Ready (200)** — existing top-level keys preserved so current observers keep
parsing; the new `migration` key adds diagnostic detail:

```json
{
  "status": "connected",
  "mlpa_version": "...",
  "pg_server_dbs": {"postgres": "connected", "app_attest": "connected"},
  "migration": {"expected": ["919c4d382c42"], "current": ["919c4d382c42"]},
  "litellm": { ... }
}
```

**Not ready (503)** — **identical key set** to the 200 body, with the failing
check(s) marked, returned via `JSONResponse(status_code=503, ...)`. Keep
`mlpa_version` and `litellm.litellm_version` present so an observer parsing them
doesn't break precisely during an incident:

```json
{
  "status": "degraded",
  "mlpa_version": "...",
  "pg_server_dbs": {"postgres": "connected", "app_attest": "offline"},
  "migration": {"expected": ["919c4d382c42"], "current": ["5b4ed32c7b2b"]},
  "litellm": {"litellm_version": "...", "status": "unreachable"}
}
```

### Liveness — unchanged

```python
@router.get("/liveness", tags=["Health"])
async def liveness_probe():
    return {"status": "alive"}
```

Readiness gates traffic; liveness gates restarts. Keeping liveness constant
guarantees a dependency blip drains the pod (readiness) without ever killing it
(liveness), so there is no crash-loop path from this change.

## Code shape

Two viable scopes:

- **One-file (matches the ticket):** inline the queries in `health.py` using the
  pools directly (`litellm_pg.pool.fetchval(...)`, `app_attest_pg.pool.fetchval(...)`)
  and module-level cached expected heads. Smallest diff, touches only
  `health.py`.
- **Cleaner (optional):** add `async def ping(self) -> bool` to `PGService`
  (replacing `check_status`) and `async def current_revisions(self)` to
  `AppAttestPGService`. Slightly wider diff, but the readiness handler reads as a
  list of named checks and the dead `_closed` path goes away. Recommended if
  touching the service layer is acceptable.

Either way the expected heads are memoized on first *successful* resolution
(module-level cache) so the Alembic `ScriptDirectory` cost is paid once, not per
probe. A failed resolution is not cached — it is retried on the next probe — so a
transient hiccup at boot doesn't pin the pod not-ready forever. Lazy
memoization keeps the change inside `health.py` and avoids any startup-ordering
coupling with `run.py`'s `lifespan`; the first probe after boot pays the
one-time cost.

## Deployment considerations (dataservices-infra)

The probe definition lives in the helm chart, not this repo. Confirm there:

- `readinessProbe.timeoutSeconds` comfortably exceeds the in-handler worst case.
  With per-check `asyncio.timeout` ≈ 2s and `gather`, the handler returns in ~2s;
  set `timeoutSeconds` to ~5 for margin. (Default is 1s — the probe *will*
  misfire without this change.) Keep the in-handler timeout strictly below
  `timeoutSeconds` so the pod returns a structured 503 instead of the kubelet
  killing the probe on its own timeout.
- `failureThreshold` / `periodSeconds` give a degraded dependency a few seconds
  of grace before draining, to ride out a transient blip.
- Liveness and readiness point at `/health/liveness` and `/health/readiness`
  respectively — verify they are not both pointed at one path.

## Testing

`src/tests/integration/test_health.py` today asserts readiness is always 200;
that expectation changes. Cases to cover:

- All dependencies healthy → 200, body reports `connected` and matching head.
- `litellm` pool query raises → 503, `litellm` pool marked `offline`.
- `app_attest` revision read raises (connection error) → 503, `app_attest` pool
  marked `offline`.
- `alembic_version` behind expected heads → 503, `migration` shows the mismatch
  (pool still `connected`).
- `alembic_version` table absent → 503, `migration.current` is `[]` (pool still
  `connected`).
- LiteLLM returns non-200 / times out → 503, `litellm` marked unreachable.
- LiteLLM returns 200 with `db != "connected"` → 503, because LiteLLM is alive
  but not ready to serve MLPA traffic.
- Liveness still returns 200 `{"status": "alive"}` under every above failure.

Mock the pool calls with `pytest-mock` and LiteLLM with `pytest-httpx`, matching
existing patterns.

## Rollout & risk

- Readiness-only change: the worst failure mode is pods draining from rotation,
  never restarting. A bug that makes the check over-strict shows up as pods going
  NotReady, which is visible and reversible (revert), not a crash-loop.
- **Boot vs steady-state.** `connect()` already `sys.exit`s on initial
  DB-connect failure (`pg_service.py:34`), so a DB down *at boot* still
  crash-loops — that path is unchanged by this plan. "Never crash-loop" here
  means *this change* adds no new restart path; it governs steady-state drain
  only.
- The migration assertion is strictly additive safety: in steady state
  current heads == expected heads, so behavior is unchanged; it only bites during the exact
  window the ticket targets — a half-applied deploy.
- The LiteLLM coupling is the main behavioral expansion to watch in the first
  deploys; the report-only downgrade above is the escape hatch if it drains too
  eagerly.

## Implementation — TDD plan

Branch: `feat-readiness-real-checks-AIPLAT-916` (off the remote default with
`--no-track`).

Scope is the **service-layer** version (the cleaner option above): the readiness
handler reads as a list of named checks and the dead `_closed` path is removed.

### TDD discipline

- Every cycle is **red → green → refactor**: write the test, watch it fail for
  the right reason (`uv run pytest -k <name>` → fail), write the *minimum* code to
  pass, then refactor under green.
- **Test and implementation land in the same commit, authored test-first.** Each
  commit is green at `make lint && make test`, so CI and `git bisect` stay sane.
  The red step lives in the dev loop, not in history — we don't commit a knowingly
  red tree.
- A refactor-only commit (commit 4) writes no new test; it is the "refactor" leg
  made safe by the suite the earlier cycles built. That is still TDD.
- The readiness contract (the integration matrix in cycle 3) is the **executable
  spec** for the feature — it is what "done" means. Cycles 1–2 build the units it
  needs; cycle 3 is written outside-in from that spec.

Test infra already in the repo: `pytest-asyncio` (`asyncio_mode=auto`),
`pytest-mock`, `pytest-httpx`. Pools are faked with `AsyncMock`;
`expected_heads` and HTTP are patched with `monkeypatch`/`pytest-httpx`.

Sequencing constraint between repos: **land the chart timing change first** (last
section). Until the probe's `timeoutSeconds` is raised, a ~2s readiness response
misfires against the 1s default and flaps pods on the MLPA deploy.

### Cycle / commit 1 — `feat(pg): add bounded ping() to PGService`

**RED** — `src/tests/unit/test_pg_service.py`:

- `test_ping_true_when_select_succeeds` — `AsyncMock` pool whose `fetchval`
  returns `1`; `connected=True` → `await svc.ping()` is `True`.
- `test_ping_false_when_query_raises` — `fetchval` raises `OSError` → `False`.
- `test_ping_false_on_timeout` — `fetchval` sleeps past the timeout (call
  `ping(timeout_s=0.01)`) → `False`.
- `test_ping_false_when_pool_none` — `pg=None` / `connected=False` → `False`.

**GREEN** — `pg_service.py`:

```python
async def ping(self, timeout_s: float | None = None) -> bool:
    if self.pg is None or not self.connected:
        return False
    try:
        async with asyncio.timeout(timeout_s or env.READINESS_CHECK_TIMEOUT_S):
            await self.pool.fetchval("SELECT 1")
        return True
    except Exception:
        logger.warning(f"readiness ping failed for /{self.db_name}")
        logger.debug(f"readiness ping failure details for /{self.db_name}", exc_info=True)
        return False
```

Keep the failure log concise. During a dependency outage this code can run every
probe interval on every pod, so avoid emitting stack traces at warning level for
expected outage symptoms; debug-level exception details are enough when needed.

**REFACTOR** — add `READINESS_CHECK_TIMEOUT_S: float = 2.0` to `config.py`, swap
the literal for `env.READINESS_CHECK_TIMEOUT_S`, add the `asyncio` import. Old
`check_status` still present and still wired into the unchanged handler → green.

### Cycle / commit 2 — `feat(migrations): expose expected heads + current revisions`

**RED** — `src/tests/unit/test_migrations.py` + additions to the app_attest
service test:

- `test_expected_heads_match_script_directory` — compute the heads independently
  with `ScriptDirectory.get_heads()` in the test and assert set equality.
  **Don't hardcode** `919c4d382c42`, so adding a migration never breaks this
  test.
- `test_expected_heads_support_multiple_heads` — patch the script directory to
  return two heads and assert both are preserved. This prevents an accidental
  regression back to `get_current_head()`, which rejects valid branched
  histories.
- `test_expected_heads_memoized` — patch `ScriptDirectory.from_config`, call
  `expected_heads()` twice, assert it was resolved once (lru_cache). Clear the
  cache in a fixture so order doesn't leak.
- `test_current_revisions_return_db_values` — mocked `fetch` returns multiple
  rows → a set of those version strings.
- `test_current_revisions_empty_when_table_absent` — `fetch` raises
  `asyncpg.UndefinedTableError` → `set()` (the "never migrated" case).
- `test_current_revisions_raise_on_connection_error` — `fetch` raises a generic
  `OSError`/timeout → **propagates** (not swallowed). This is the
  app_attest-pool-down signal the cycle-3 handler maps to `offline`; it must be
  distinguishable from the `set()`/table-absent case above. Asserts the two
  failure modes don't collapse into one.

**GREEN** — new `src/mlpa/core/migrations.py`:

```python
from functools import lru_cache
from pathlib import Path
import mlpa
from alembic.config import Config
from alembic.script import ScriptDirectory

_ALEMBIC_DIR = Path(mlpa.__file__).resolve().parents[2] / "alembic"

@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if not heads:
        raise RuntimeError("could not resolve Alembic heads")
    return frozenset(heads)
```

`lru_cache` memoizes only on success — an exception isn't cached, so a transient
boot failure is retried on the next probe.

`app_attest_pg_service.py`:

```python
async def current_revisions(self, timeout_s: float | None = None) -> set[str]:
    try:
        async with asyncio.timeout(timeout_s or env.READINESS_CHECK_TIMEOUT_S):
            rows = await self.pool.fetch("SELECT version_num FROM alembic_version")
            return {row["version_num"] for row in rows}
    except asyncpg.UndefinedTableError:
        return set()  # never migrated
```

(A timeout/connection error propagates and is treated as a failed check by the
caller in cycle 3.)

**REFACTOR** — none beyond tidy imports. Nothing wired in yet → green.

### Cycle / commit 3 — `feat(health): readiness runs real dependency checks (AIPLAT-916)`

The one behavior-changing commit, written **outside-in** from the contract.

**RED** — rewrite `src/tests/integration/test_health.py` to the executable spec
(this replaces the current always-200 assertion, which goes red first):

- `test_readiness_200_when_all_healthy` — body reports `connected`, `migration`
  expected == current, both serialized as sorted lists for stable JSON.
- `test_readiness_503_when_litellm_pool_down` — `litellm_pg.ping` → `False`.
- `test_readiness_503_when_app_attest_pool_down` — `app_attest_pg.current_revisions`
  raises a connection error → body `pg_server_dbs.app_attest` == `offline`.
  (No separate `app_attest_pg.ping` — app_attest liveness is the revision read,
  per Design §1/§2.)
- `test_readiness_503_when_migration_behind` — `current_revisions` returns an
  old revision set; body `migration` shows the mismatch (pool is `connected`).
- `test_readiness_503_when_database_has_unknown_head` — `current_revisions`
  returns a head not present in `expected_heads`; body shows the mismatch. This
  covers accidental rollback/newer-DB cases.
- `test_readiness_503_when_alembic_table_absent` — `current_revisions` → `set()`;
  body `migration.current` == `[]`, pool still `connected`. Distinct from the
  pool-down case above — assert both body shapes, not just the 503.
- `test_readiness_503_when_litellm_non_200` — `pytest-httpx` 503.
- `test_readiness_503_when_litellm_db_not_connected` — `pytest-httpx` 200 with
  `{"status": "healthy", "db": "disconnected"}` or `"Not connected"` → 503.
- `test_readiness_503_when_litellm_times_out` — `httpx_mock` raises timeout.
- `test_readiness_503_when_heads_unresolvable` — patch `expected_heads` to raise.
- `test_liveness_200_under_degraded_dep` — liveness stays `{"status":"alive"}`
  while a dependency is down.

**GREEN** — rewrite `readiness_probe` in `health.py`. Three independent checks
under `asyncio.gather(..., return_exceptions=True)`:

1. `litellm_pg.ping()` — the `litellm` pool's live query.
2. `app_attest_pg.current_revisions()` — doubles as the `app_attest` liveness
   check: a returned set (including `set()` for table-absent) ⇒ pool `connected`; a
   raised connection/timeout ⇒ pool `offline`. Compared against `expected_heads()`
   (resolution failure → not ready, logged).
3. LiteLLM HTTP requiring `status_code == 200`, `db == "connected"`, and a known
   healthy top-level status (`"healthy"` or `"connected"`).

Bind each `gather` result to a **named local** (`litellm_ok`, `revisions`,
`litellm_http`) and assemble the body from those — do **not** index the gather
list positionally. With `return_exceptions=True` a raised check is an `Exception`
object in the list, and positional unpacking silently mis-labels results if a
check is ever reordered or removed. `JSONResponse(status_code=503, ...)` if any
check fails (same key set as the 200 body, per the contract), else the 200 body.
Liveness untouched.

**REFACTOR** — extract body assembly into a small helper so the handler reads as
the three named checks; keep it inside `health.py`.

### Cycle / commit 4 — `refactor(pg): drop dead check_status/_closed flag`

The **refactor leg** the green suite now makes safe — no new test.

- Remove `check_status` from `pg_service.py` (no callers after cycle 3) and any
  now-unused imports. `make test` green proves nothing depended on it.

### Commit 5 — `docs: AIPLAT-916 readiness design + TDD plan`

- This document. Can lead the series if review prefers design-first.

### Optional de-risking — feature flag

Prefer not to add a flag if the chart timing change can land first; readiness is
already revert-reversible, and preserving known-bad always-200 behavior adds a
config path and test matrix that can linger. Use a temporary
`MLPA_READINESS_STRICT` only if an environment cannot guarantee the chart change
lands before the app change. If added, drive it test-first: parametrize the
cycle-3 matrix over the flag (`false` → current always-200; `true` → the 503
cases), flip per environment after validation, then remove the flag in a
follow-up.

## Separate PR — chart probe timing (dataservices-infra)

Lands **before** the MLPA series reaches an environment:

- Raise `readinessProbe.timeoutSeconds` to ~5 (from the 1s default).
- Set `failureThreshold` / `periodSeconds` to allow a few seconds of grace before
  draining a transiently-degraded pod.
- Verify `livenessProbe` points at `/health/liveness` and `readinessProbe` at
  `/health/readiness` (not both at one path).
