# Mozilla LLM Proxy Auth (MLPA)

Authenticates and proxies LLM requests through LiteLLM to enact budgets and per-user management.

Auth strategies supported:

* Firefox Account Auth
* iOS App Attest
* Google Play Integrity

## Setup

```bash
make setup
```

This creates a virtual environment in `.venv/`, installs dependencies, and installs the tool locally in editable mode.

# Running MLPA locally with Docker

### Run LiteLLM and PostgreSQL

1. `docker compose -f litellm_docker_compose.yaml up -d`

    (`docker compose -f litellm_docker_compose.yaml down --volumes --remove-orphans` to remove all)

    Privacy Filter is disabled by default in MLPA. To run it locally, set
    `PRIVACY_FILTER_ENABLED=true` in `.env`; `start.sh` reads this and enables
    the Docker Compose `privacy-filter` profile automatically.
    For this you'll need to first build the [privacy-filter](https://github.com/Firefox-AI/privacy-filter) image yourself.

    When disabled, MLPA also skips the Privacy Filter dependency in `/health/readiness`.

### Create and migrate appattest database

2. `sh ./scripts/create-app-attest-database.sh`

3. Migrate app_attest (repo root): `bash scripts/migrate-app-attest-database-local.sh` or `uv run alembic upgrade head`

4. Set `MLPA_DEBUG=true` in the `config.py` or `.env` file

### Create a virtual LiteLLM key

5. Run `python scripts/create-and-set-virtual-key.py` (also sets the value in `.env`)

### Run MLPA

6. Install it as a library:

```bash
pip install --no-cache-dir -e .
```

7. Run the binary

```bash
mlpa
```

Navigate to

## Config (see [LiteLLM Documentation](https://docs.litellm.ai/docs/simple_proxy_old_doc) for more config options)

### See `config.py` for all configuration variables

### Also See `litellm_config.yaml` for litellm config

Service account configured to hit VertexAI: `service_account.json` should be in directory root

## API Documentation

After running, Swagger can be viewed at `http://localhost:<PORT>/api/docs`

## Auth benchmarks

`src/tests/bench/` times each auth path with [pytest-benchmark](https://pytest-benchmark.readthedocs.io/). FxA, Google, Apple and Postgres are replaced with in-memory fakes, but the real crypto runs (RS256 FxA tokens, HS256 MLPA access tokens, App Attest ECDSA assertions). The suite covers:

- `fxa_auth` with a stub client (MLPA's own overhead) and with the real pyfxa client, both on a cache miss and on a cache hit
- `fxa_auth` under concurrent requests to a client that blocks for 5 ms, which catches FxA calls that block the event loop (this one does not track CPU cost)
- dev auth (`auth_with_key`)
- the MLPA access-token check used after Play Integrity
- App Attest `verify_assert` and the full `app_attest_auth`, including the challenge check
- `authorize_chat_request` dispatch for each auth header combination, with the auth backends stubbed out

### Running

```sh
make bench          # run the suite once (a few seconds)
make bench-compare  # compare with origin/main the way CI does
make bench-compare ARGS="--base my-branch --runs 3"
```

`make test` skips the benchmarks. They run only with `--benchmark-only`.

### CI and reading the results

The `bench` workflow runs on PRs that touch `src/`, `pyproject.toml`, `uv.lock`, `scripts/bench_compare.py` or the workflow. `scripts/bench_compare.py` checks out the base branch in a git worktree and copies the PR's `src/tests/bench/` into it. Each side gets its own virtualenv from its own `uv.lock`, so dependency bumps are measured too. The script then runs the suite `BENCH_RUNS` times per side, alternating between base and PR.

The job summary has one row per bench. Each value is the time per operation: the median of the per-run medians, divided by the number of operations batched into each round.

| Result | Meaning |
|---|---|
| ✅ | Within the threshold. |
| ❌ | The PR is more than the threshold slower than base. The job fails. |
| ⚠️ | The bench failed on the PR side. The job fails. |
| 🆕 | The bench is new and has no baseline on base. |

CI machines vary by 10–30% from run to run, so a single run per side gives false alarms. Repeating whole runs and comparing medians filters out that noise. If the job fails, rerun it once. A real regression fails again with a similar change.

### Threshold and runs

| Setting | Default | Change it with |
|---|---|---|
| Fail threshold | `20%` | Repository variable `BENCH_FAIL_THRESHOLD`, or `--threshold` |
| Runs per side | `5` | Repository variable `BENCH_RUNS`, or `--runs` |
| Rounds per bench | `50` | `ROUNDS` in `src/tests/bench/harness.py` |

Set the repository variables under **Settings → Secrets and variables → Actions → Variables**. A lower threshold catches smaller regressions but needs more runs to stay quiet on unchanged code. Rerun an unchanged branch several times before lowering it.

Because the workflow uses a `paths` filter, it does not run on PRs that change only docs. If you make it a required check, allow skipped runs in the branch protection rules.

### Adding a bench

1. Add a `test_bench_*.py` file in `src/tests/bench/`. Keep fakes in `src/tests/bench/fakes.py`, not in other test folders, because the base side runs this folder too.
2. Use `run_async` or `run_sync` from `tests.bench.harness`. Batch enough operations per round that a round takes at least about 100 µs.
3. Assert on the result inside the timed function, so a bench that silently takes a different code path fails instead of reporting a misleading time.
4. If the bench targets code that does not exist on `main` yet, load it with `require("module.path", "name")`. On the base side that reports "no baseline" instead of failing.
