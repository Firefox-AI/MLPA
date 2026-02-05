# Guidelines for Contributions

## Local setup

### Dev requirements

We recommend using [uv](https://docs.astral.sh/uv/getting-started/installation/):

1. `make`
2. `source .venv/bin/activate`

### Lint

Ensure all checks pass:

`pre-commit run --all-files --verbose`

## Installation

`make install`

## Tests

Ensure all tests pass: `pytest -v`

## Local Build for QA and manual testing

1. Use `litellm_docker_compose.yaml` to start LiteLLM and Postgres locally:

```bash
docker compose -f litellm_docker_compose.yaml up -d
```

or if you are using legacy docker-compose:

```bash
docker-compose -f litellm_docker_compose.yaml up -d
```

2. Create a second database that is needed for authentication, and apply migrations

```bash
bash scripts/create-app-attest-database.sh
```

```bash
alembic upgrade head
```

LiteLLM will be accessible at `localhost:4000` and `localhost:4000/ui`.

3. Run MLPA with

```bash
mlpa
```

4. Stop the service with

```bash
docker compose -f litellm_docker_compose.yaml down
```

### Useful CURLs for QA

1. MLPA liveness:

```bash
curl --location 'http://0.0.0.0:8080/health/liveness' \
--header 'Content-Type: application/json'
```

1. MLPA readiness:

```bash
curl --location 'http://0.0.0.0:8080/health/readiness' \
--header 'Content-Type: application/json'
```

1. MLPA completion:

```bash
curl --location 'http://0.0.0.0:8080/v1/chat/completions' \
--header 'Content-Type: application/json' \
--header 'authorization: Bearer {YOUR_MOZILLA_FXA_TOKEN}' \
--header 'X-LiteLLM-Key: Bearer {MASTER_KEY}' \
--data '{
 "model": "openai/gpt-4o",
  "messages": [{
     "role": "user",
     "content": "Hello!"
   }]
}'
```

1. LiteLLM liveness:

```bash
curl --location 'http://localhost:4000/health/liveness' \
--header 'Content-Type: application/json'
```

1. List of available models:

```bash
curl --location 'http://localhost:4000/models' \
--header 'Content-Type: application/json' \
--header 'X-LiteLLM-Key: Bearer {MASTER_KEY}' \
--data ''
```

1. Completion directly from LiteLLM:

```bash
curl --location 'http://localhost:4000/v1/chat/completions' \
--header 'Content-Type: application/json' \
--header 'X-LiteLLM-Key: Bearer {MASTER_KEY}' \
--data '{
    "model": "openai/gpt-4o",
    "messages": [
      {
        "role": "user",
        "content": "what is 2+2?"
      }
    ]
}'
```

## FXA tokens and where to find them

MLPA uses the [https://github.com/mozilla/PyFxA](https://github.com/mozilla/PyFxA) library for authentication with a Mozilla account. Please follow the quick-start instructions in their [README](https://github.com/mozilla/PyFxA?tab=readme-ov-file#using-firefox-account-bearer-token-with-requests).

Here is a quick snippet:

```python

from fxa.tools.bearer import get_bearer_token

fxa_token: str = get_bearer_token(
    your_mozilla_account_email,
    your_mozilla_account_password,
    scopes=["profile:uid"],
    client_id="5882386c6d801776" # a common client_id for the dev environment,
    account_server_url="https://api.accounts.firefox.com",
    oauth_server_url="https://oauth.accounts.firefox.com",
)
```

## How to update static docs/index.html from redoc

Ensure Node is installed

1. `make install`
2. `mlpa`
3. `curl http://localhost:8080/openapi.json -o /tmp/openapi.json`
4. `npx @redocly/cli build-docs /tmp/openapi.json -o docs/index.html`
