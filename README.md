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

    Privacy Filter is enabled by default in MLPA. If you use `start.sh`, it reads
    `.env` and enables the Docker Compose `privacy-filter` profile automatically.
    For this you'll need to first build the [privacy-filter](https://github.com/Firefox-AI/privacy-filter) image yourself.

    To run MLPA without Privacy Filter locally, set `PRIVACY_FILTER_ENABLED=false`
    in `.env`. This also skips the Privacy Filter dependency in `/health/readiness`.

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
