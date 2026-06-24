# Database management

MLPA talks to two PostgreSQL databases. This doc covers what lives where, how the
connection pools are configured, and the query timeout budgets (why we have a few
of them and which query uses which).

## The two databases

| DB | Owner | What MLPA does with it |
|----|-------|------------------------|
| `litellm` (`LITELLM_DB_NAME`) | LiteLLM | Reads/writes a couple of tables directly for things the free-tier LiteLLM API doesn't expose (block/unblock, budget tier change, user listing/counts). |
| `app_attest` (`APP_ATTEST_DB_NAME`) | MLPA (via Alembic) | App Attest challenges + keys, and the signup capacity state. |

Each DB gets its own asyncpg pool, wrapped in a `PGService`
(`src/mlpa/core/pg_services/`):

- `LiteLLMPGService` → `litellm`
- `AppAttestPGService` → `app_attest` (also holds a reference to the litellm
  service, because the capacity gate reads from both)

## Tables

### litellm DB (LiteLLM owns the schema)

**`LiteLLM_EndUserTable`** - one row per end user.

- `user_id` is `{base_identity}:{service_type}`, e.g. `fxa_uid:ai`. The colon is
  load-bearing, we `split_part(user_id, ':', ...)` all over the place.
- MLPA uses: `user_id`, `budget_id` (which tier the user is on), `blocked`.
- Touched by: `get_user`, `list_users`, `update_user_budget`, `block_user`,
  `count_users_by_service_type`, `list_managed_base_identities`,
  `has_managed_user_rows`.

**`LiteLLM_BudgetTable`** - the budget tiers (one per service type).

- `budget_id`, `max_budget`, `rpm_limit`, `tpm_limit`, `budget_duration`.
- MLPA upserts all tiers from config on startup (`create_budget()`), so changing
  a limit in `config.py` takes effect on next restart, not live.

### app_attest DB (MLPA owns the schema, managed by Alembic)

**`challenges`** - App Attest challenge nonce.

| Column | Type | Notes |
|--------|------|-------|
| `key_id_b64` | `VARCHAR(255)` PK | the attested key id |
| `challenge` | `VARCHAR(255)` | the nonce we issued |
| `created_at` | `TIMESTAMPTZ` | expires after `CHALLENGE_EXPIRY_SECONDS` (300s) |

**`public_keys`** - the iOS attested key + replay counter.

| Column | Type | Notes |
|--------|------|-------|
| `key_id_b64` | `VARCHAR(255)` PK | |
| `public_key_pem` | `TEXT` | attested public key |
| `counter` | `BIGINT` | assertion counter, only goes up (replay protection) |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

**`mlpa_user_capacity`** - the signup cap counter. Single row.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `SMALLINT` PK `CHECK (id = 1)` | singleton, always 1 |
| `max_identities` | `BIGINT` | the cap (`MLPA_MAX_SIGNED_IN_USERS`) |
| `current_identities` | `BIGINT` | how many distinct identities are claimed |
| `updated_at` | `TIMESTAMPTZ` | |

**`mlpa_user_capacity_identities`** - one row per claimed identity.

| Column | Type | Notes |
|--------|------|-------|
| `base_identity` | `TEXT` PK | the `{base_identity}` part of `user_id` |
| `created_at` | `TIMESTAMPTZ` | |

The two capacity tables are reconciled from `LiteLLM_EndUserTable` on startup
(`ensure_capacity_state()`), so they don't drift from the real user base.

## Connection pool

Set up in `PGService.connect()`, configured from `config.py`:

| Setting | Default | What |
|---------|---------|------|
| `PG_POOL_MIN_SIZE` | 1 | min connections |
| `PG_POOL_MAX_SIZE` | 10 | max connections |
| `PG_PREPARED_STMT_CACHE_MAX_SIZE` | 100 | prepared statement cache |

On connect we set these server-side (per session, so they apply to every query
on the pool):

- `statement_timeout` = `PG_STATEMENT_TIMEOUT_MS`
- `idle_in_transaction_session_timeout` = `PG_IDLE_IN_TX_TIMEOUT_MS`
- `application_name` = `mlpa:{db_name}` (handy for `pg_stat_activity`)

## Timeout budgets

The idea: keep a tight default so a runaway query gets killed by Postgres even if
the client or event loop hangs (no connection pile-up). Then raise the budget
only for the few queries that legitimately need longer.

| Budget | Default | Used for |
|--------|---------|----------|
| `PG_STATEMENT_TIMEOUT_MS` | 3000 (3s) | pool default, every query unless raised |
| `PG_IDLE_IN_TX_TIMEOUT_MS` | 10000 (10s) | reaps sessions left idle mid-transaction |
| `PG_ADMIN_READ_TIMEOUT_MS` | 15000 (15s) | admin reads that full-scan the user table |
| `PG_MAINTENANCE_STATEMENT_TIMEOUT_MS` | 30000 (30s) | startup reconciliation (bigger scans) |
| `MLPA_ADMISSION_LOCK_TIMEOUT_MS` | 5000 (5s) | `lock_timeout` for the capacity row `FOR UPDATE` |
| `PG_COMMAND_TIMEOUT_S` | None | optional asyncpg client-side backstop, off by default |

All values are ms (except `PG_COMMAND_TIMEOUT_S`, which is seconds). 0 = unlimited.

### How a budget gets applied

Two ways:

1. **Pool-wide** via `server_settings` (the 3s `statement_timeout` and 10s
   idle-in-tx). This is the baseline for everything.
2. **Per-transaction** via `SET LOCAL`, using two context managers in
   `PGService`. `SET LOCAL` only lasts for the transaction, so the connection
   goes back to the pool defaults on release.

   - `statement_timeout(ms)` - raises `statement_timeout` AND idle-in-tx to the
     same `ms`. idle-in-tx has to match, otherwise the 10s reaper could kill a
     transaction we deliberately gave a longer budget.
   - `admission_transaction()` - the capacity gate path. Sets `lock_timeout` =
     `MLPA_ADMISSION_LOCK_TIMEOUT_MS`, and `statement_timeout` = `lock_timeout +
     PG_STATEMENT_TIMEOUT_MS` (so 5s + 3s = 8s). The statement budget has to sit
     above the lock budget, because Postgres counts lock-wait time toward
     `statement_timeout`. If it didn't, the 3s default would cap the lock wait
     before `lock_timeout` ever fired.

### Which query uses which budget

| Budget | Queries |
|--------|---------|
| default 3s | challenge + key CRUD, `get_user`, `update_user_budget`, `block_user`, `create_budget` upsert |
| admin-read 15s | `list_users` (COUNT(*) + deep OFFSET), `count_users_by_service_type` (GROUP BY `split_part`), `has_managed_user_rows` (EXISTS) |
| maintenance 30s | `list_managed_base_identities` (DISTINCT scan), `_reconcile_capacity_claims` (bulk DELETE + INSERT) |
| admission 8s | `admit_managed_base_identity`, `maybe_release_managed_base_identity_if_no_managed_users` |

The admin-read and maintenance ones all hit the same problem: the `user_id` is
`base:service_type`, so any filter or group on the service-type part uses
`split_part`/`position`, which is unindexable. That means a full-table scan that
grows with the user base and can blow past 3s. So they get a bigger budget
instead.

### Cross-pool read ordering

The capacity reconcile and release paths read from the litellm pool and then open
a transaction on the app_attest pool. That read always happens BEFORE the
app_attest transaction opens. If you did it inside, the app_attest session would
sit idle-in-transaction across the cross-pool `await`, and the idle-in-tx reaper
could kill it (aborting the work and leaking a capacity claim). See
`_reconcile_capacity_claims` and `maybe_release_managed_base_identity_if_no_managed_users`.

### Client-side backstop

`PG_COMMAND_TIMEOUT_S` is asyncpg's own client-side cancel and it's off by
default. Careful: it is NOT relaxed by the per-transaction `SET LOCAL` budgets. If
you turn it on, set it above `PG_MAINTENANCE_STATEMENT_TIMEOUT_MS` (30s) or it
will cancel the maintenance/admin reads.

### These timeouts only apply to MLPA

All of the above is set as asyncpg `server_settings` on MLPA's own connection
pools, at connect time. It's per-session, not database-wide and not on the DB
role. Nothing in the migrations or scripts sets `statement_timeout` at the
`ALTER DATABASE` / `ALTER ROLE` level.

So anything else that connects to these databases on its own session is NOT
affected. That includes the cleanup cron job in the llm-proxy infra, LiteLLM
itself, and the Cloud SQL console. They run with the Postgres default (usually
unlimited) unless someone sets a timeout for that role separately. The cron job
can take as long as it needs, the 3s default won't touch it.

## Migrations

Alembic manages the `app_attest` DB only. LiteLLM manages its own schema.

```bash
uv run alembic upgrade head      # apply
uv run alembic downgrade -1      # roll back one
uv run alembic revision -m "..." # new migration
```

The `mlpa_user_capacity*` tables are created by migration, then reconciled on
every startup via `ensure_capacity_state()`. Deploy runs
`scripts/migrate-app-attest-database.sh` with `-x sqlalchemy.url=...`.

## Startup work

The `lifespan` in `run.py` does two DB things on boot:

1. `litellm_pg.create_budget()` - upsert all budget tiers from config.
2. `app_attest_pg.ensure_capacity_state()` - seed the singleton capacity row
   (fatal if it fails, without the row every admission 500s), then reconcile the
   claim table (best-effort, if it fails the row keeps a stale count and
   admissions still work).
