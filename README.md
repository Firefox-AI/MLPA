# Mozilla LLM Proxy Auth (MLPA)

A proxy to verify App Attest/FxA payloads and proxy requests through any-llm-gateway to enact budgets and per user management.

## Setup

```bash
make setup
```

This creates a virtual environment in `.venv/`, installs dependencies, and installs the tool locally in editable mode.

## Running MLPA locally with Docker

### Run Any-LLM-Gateway

The any-llm-gateway image requires authentication to pull: see [github docs](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-with-a-personal-access-token-classic) for help with creating a PAT and authenticating docker to the registry.
```bash
echo $GITHUB_PAT | docker login ghcr.io -u USERNAME --password-stdin # The command to authenticate docker with ghcr
docker compose -f anyllm_docker_compose.yaml up -d
```

### Run MLPA

1. install it as a library

```bash
pip install --no-cache-dir -e .
```

2. Run the binary

```bash
mlpa
```

## Config

`.env` (see `config.py` for all configuration variables)

```
MASTER_KEY="sk-1234..."
GATEWAY_API_BASE="http://any-llm-gateway:8000"
DATABASE_URL=postgresql://gateway:gateway@postgres:5432
CHALLENGE_EXPIRY_SECONDS=300
PORT=8080

APP_BUNDLE_ID="org.example.app"
APP_DEVELOPMENT_TEAM="12BC943KDC"

CLIENT_ID="..."
CLIENT_SECRET="..."

MODEL_NAME="vertexai:model-name"  # Use provider:model format
TEMPERATURE=0.1
TOP_P=0.01
```

### Gateway Configuration

See `gateway_config.yaml` for any-llm-gateway configuration.

Service account configured to hit VertexAI: `service_account.json` should be in directory root

## API Documentation

After running, Swagger can be viewed at `http://localhost:<PORT>/api/docs`

## Useful Prometheus queries

| Metric Description                       | Query                                                                                                                                                                                  |
| :--------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Total requests (RPS)**                 | `sum(rate(requests_total{endpoint!~"/metrics"}[5m]))`                                                                                                                                  |
| **Requests per endpoint (RPS)**          | `sum by (method, endpoint) (rate(requests_total{endpoint!~"/metrics"}[5m]))`                                                                                                           |
| **Requests currently in progress**       | `sum(in_progress_requests{endpoint!~"/metrics"})`                                                                                                                                      |
| **Response status codes (RPS)**          | `sum by (status_code) (rate(response_status_codes_total[5m]))`                                                                                                                         |
| **Error rate (5xx)**                     | `sum(rate(response_status_codes_total{status_code=~"5.."}[5m])) / sum(rate(requests_total{endpoint!~"/metrics"}[5m]))`                                                                 |
| **Overall average request latency**      | `sum(rate(request_latency_seconds_sum{endpoint!~"/metrics"}[5m])) / sum(rate(request_latency_seconds_count{endpoint!~"/metrics"}[5m]))`                                                |
| **Average request latency per endpoint** | `sum by (method, endpoint) (rate(request_latency_seconds_sum{endpoint!~"/metrics"}[5m])) / sum by (method, endpoint) (rate(request_latency_seconds_count{endpoint!~"/metrics "}[5m]))` |
| **Challenge validation latency**         | `rate(validate_challenge_latency_seconds_sum[5m]) / rate(validate_challenge_latency_seconds_count[5m])`                                                                                |
| **App Attest auth latency by result**    | `sum by (result) (rate(validate_app_attest_latency_seconds_sum[5m])) / sum by (result) (rate(validate_app_attest_latency_seconds_count[5m]))`                                          |
| **App Assert auth latency by result**    | `sum by (result) (rate(validate_app_assert_latency_seconds_sum[5m])) / sum by (result) (rate(validate_app_assert_latency_seconds_count[5m]))`                                          |
| **FxA authentication latency by result** | `sum by (result) (rate(validate_fxa_latency_seconds_sum[5m])) / sum by (result) (rate(validate_fxa_latency_seconds_count[5m]))`                                                        |
| **Chat completion latency by result**    | `sum by (result) (rate(chat_completion_latency_seconds_sum[5m])) / sum by (result) (rate(chat_completion_latency_seconds_count[5m]))`                                                  |
| **Time to first token (TTFT)**           | `rate(chat_completion_ttft_seconds_sum[5m]) / rate(chat_completion_ttft_seconds_count[5m])`                                                                                            |
| **Tokens per chat request by type**      | `sum(rate(chat_tokens_total[5m])) by (type) / on() group_left() sum(rate(chat_completion_latency_seconds_count[5m]))`                                                                  |
| **Total tokens per chat request**        | `sum(rate(chat_tokens_total[5m])) / sum(rate(chat_completion_latency_seconds_count[5m]))`                                                                                              |
