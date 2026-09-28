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

MLPA requires Python 3.12 (pinned in `.python-version`). `jwtoxide` has no wheels for newer versions and fails to build from source.

# Running MLPA locally with Docker

### Prerequisites

- `.env` in the repo root with at least:

    ```bash
    MASTER_KEY=sk-<any-value>   # LiteLLM master key
    MLPA_DEBUG=true
    ```

    `OPENAI_API_KEY` and `EXA_API_KEY` are only needed for those models.

- `service_account.json` in the repo root, with Vertex AI access to `fx-gen-ai-sandbox`. Either a service account key or your own credentials:

    ```bash
    gcloud auth application-default login
    cp ~/.config/gcloud/application_default_credentials.json service_account.json
    ```

    Create it before starting Docker. Otherwise Docker creates an empty directory in its place and LiteLLM fails to start.

### Run LiteLLM and PostgreSQL

1. `docker compose -f litellm_docker_compose.yaml up -d`

    To stop: `docker compose -f litellm_docker_compose.yaml down`. Adding `--volumes` also deletes the database data.

    Privacy Filter is disabled by default in MLPA. To run it locally, set
    `PRIVACY_FILTER_ENABLED=true` in `.env`; `start.sh` reads this and enables
    the Docker Compose `privacy-filter` profile automatically.
    For this you'll need to first build the [privacy-filter](https://github.com/Firefox-AI/privacy-filter) image yourself.

    When disabled, MLPA also skips the Privacy Filter dependency in `/health/readiness`.

### Create and migrate the app_attest database

2. `bash scripts/migrate-app-attest-database-local.sh`

    Creates the database if needed, runs Alembic, and seeds the user capacity row.

### Create a virtual LiteLLM key

3. `uv run python scripts/create-and-set-virtual-key.py`

    Writes `MLPA_VIRTUAL_KEY` to `.env`.

### Run MLPA

4. `.venv/bin/mlpa` (or `make mlpa`)

    Serves on `http://localhost:8080`. Swagger is at `/api/docs`.

`make docker-up` runs steps 1–3.

### Send a test request

Without a real FxA token, sign a local MLPA access token with the default dev secret:

```bash
TOKEN=$(uv run python -c "from mlpa.core.utils import issue_mlpa_access_token as t; print(t('local-test-user'))")
curl localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'use-play-integrity: true' -H 'service-type: ai' \
  -H 'Content-Type: application/json' \
  -d '{"model":"vertex_ai/mistral-small-2503","messages":[{"role":"user","content":"hi"}]}'
```

## Config (see [LiteLLM Documentation](https://docs.litellm.ai/docs/simple_proxy_old_doc) for more config options)

### See `config.py` for all configuration variables

### Also See `litellm_config.yaml` for litellm config

Service account configured to hit VertexAI: `service_account.json` should be in directory root

## API Documentation

After running, Swagger can be viewed at `http://localhost:<PORT>/api/docs`
