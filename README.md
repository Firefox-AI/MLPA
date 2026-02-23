# Mozilla LLM Proxy Auth (MLPA)

A proxy to verify App Attest/FxA payloads and proxy requests through LiteLLM to enact budgets and per user management.

## Setup

```bash
make setup
```

This creates a virtual environment in `.venv/`, installs dependencies, and installs the tool locally in editable mode.

# Running MLPA locally with Docker

### Run LiteLLM and PostgreSQL

1. `docker compose -f litellm_docker_compose.yaml up -d`

### Create and migrate appattest database

2. `sh ./scripts/create-app-attest-database.sh`

3. `alembic upgrade head`

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
| **FxA authentication latency by source** | `sum by (verification_source) (rate(validate_fxa_latency_seconds_sum[5m])) / sum by (verification_source) (rate(validate_fxa_latency_seconds_count[5m]))`                              |
| **FxA verifications by source (RPS)**    | `sum by (verification_source) (rate(fxa_verifications_total[5m]))`                                                                                                                       |
| **Chat completion latency by result**    | `sum by (result) (rate(chat_completion_latency_seconds_sum[5m])) / sum by (result) (rate(chat_completion_latency_seconds_count[5m]))`                                                  |
| **Time to first token (TTFT)**           | `rate(chat_completion_ttft_seconds_sum[5m]) / rate(chat_completion_ttft_seconds_count[5m])`                                                                                            |
| **Tokens per chat request by type**      | `sum(rate(chat_tokens_total[5m])) by (type) / on() group_left() sum(rate(chat_completion_latency_seconds_count[5m]))`                                                                  |
| **Total tokens per chat request**        | `sum(rate(chat_tokens_total[5m])) / sum(rate(chat_completion_latency_seconds_count[5m]))`                                                                                              |
