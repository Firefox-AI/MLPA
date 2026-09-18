# Traffic contracts

## TL;DR

Traffic contracts classify feature traffic against shared Redis fixed-window
request and token counters. Windows default to 60 seconds. Each feature has an
RPM and TPM contract, and all participating features share a total **basket**.

The current behavior is observational: over-contract requests continue. There is
no contract-based HTTP 429 or soft degradation yet. Decisions are logged and
attached to `mlpa_requests_total` as `traffic_contract_rpm_mode` and
`traffic_contract_tpm_mode`.

| Mode | Meaning | `allowed` |
| --- | --- | --- |
| `normal` | Neither applicable limit is exceeded | `True` |
| `borrowed` | Feature exceeds its contract, but the basket does not exceed its limit | `False` |
| `degraded` | Basket exceeds its limit, regardless of feature usage | `False` |

`allowed=False` describes the contract decision; it does not currently stop the
request. RPM and TPM receive independent decisions and can have different modes.

The flow is **check RPM/TPM → call upstream → increment RPM/TPM**. Checking and
incrementing are separate because actual token usage is only available after the
upstream response. Both `/v1/chat/completions` and `/v1/search` implement the full flow.

## Configuration and feature mapping


`env.traffic_contract_config` joins `env.service_type_config` with the feature contract limits.
It is keyed by service type for request lookup, but Redis stores counts by
**feature**, so service types mapped to the same feature share one counter.

| Feature, with default name | Service types | Contract settings |
| --- | --- | --- |
| `smart-window` | `ai`, `memories`, `search`, `answer`, `sw-answer`, `liner-answer`, `telemetry`, `agent`, `agent-search`, `ai-dev`, `memories-dev`, `mochi-dev`, `search-dev` | `SMART_WINDOW_TRAFFIC_CONTRACT_RPM_LIMIT`, `SMART_WINDOW_TRAFFIC_CONTRACT_TPM_LIMIT` |
| `s2s` | `s2s` | `S2S_TRAFFIC_CONTRACT_RPM_LIMIT`, `S2S_TRAFFIC_CONTRACT_TPM_LIMIT` |
| `s2s-android` | `s2s-android` | `S2S_ANDROID_TRAFFIC_CONTRACT_RPM_LIMIT`, `S2S_ANDROID_TRAFFIC_CONTRACT_TPM_LIMIT` |
| Shared basket | All features whose traffic is counted | `TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT`, `TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT` |

Feature names come from `FEATURE_SMART_WINDOW`, `FEATURE_S2S`, and
`FEATURE_S2S_ANDROID`. All contract limits default to `0`, meaning no limit for
that dimension. A disabled feature limit does not disable an enabled basket limit,
and vice versa. Counters still increment when the feature is configured and
contract tracking is enabled, even if its limits are zero.

**Note:** These limits are separate from the per-user `rpm_limit` and `tpm_limit` entries in
`service_type_config`, which are used for LiteLLM user budgets. A request classified
as `normal` can still be rejected by those other controls.

| Setting | Default | Purpose |
| --- | --- | --- |
| `ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT` | `False` | Enable Redis connection, checks, and counter updates; does not enable over-contract rejection |
| `TRAFFIC_CONTRACT_REDIS_KEY_PREFIX` | `mlpa:traffic_contract` | Namespace for contract keys |
| `TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS` | `60` | Fixed request-count window |
| `TRAFFIC_CONTRACT_TPM_WINDOW_SECONDS` | `60` | Fixed token-count window |
| `TRAFFIC_CONTRACT_COUNTER_TTL_SECONDS` | `120` | Expiration refreshed by each positive increment |
| `TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR` | `True` | Continue after a check failure; log update failures |
| `REDIS_HOST`, `REDIS_PORT` | `localhost`, `6379` | Redis connection used by MLPA |

With non-default window lengths, the configured limits apply per configured
window; they are not automatically rescaled to a minute.

## Request flow

[`enforce_traffic_contract()`](../src/mlpa/core/middleware/traffic_contract_enforcer.py)
lives in the middleware package, but it is called explicitly by the chat and
search route handlers in [`run.py`](../src/mlpa/run.py). It runs after FastAPI has
resolved `authorize_chat_request` or `authorize_search_request` and before user
resolution and the upstream call.

Using a route-level check lets it reuse validated service-type and authorization
information instead of duplicating that logic in HTTP middleware. Requests that
fail authorization never reach the check and do not increment contract counters.
They can still appear in the ordinary HTTP request metrics.

### Before the upstream call

The enforcer initializes both request-state modes to `N/A`. If tracking is disabled
or the service type has no contract, it returns without checking Redis.

Otherwise, `check_feature_traffic_contracts()` runs one Lua script against the RPM
and TPM keys. For each dimension it:

1. Reads the feature field and `__basket__` field, treating missing values as zero.
2. Adds a proposed increment **for comparison only**: `1` for RPM, `0` for TPM.
3. Checks the basket first. If its positive limit is exceeded, returns `degraded`.
4. Otherwise checks the feature. If its positive limit is exceeded, returns `borrowed`.
5. Otherwise returns `normal`.

The comparison is strictly `count > limit`: equality is within contract. When
both limits for a dimension are zero, the script short-circuits to `normal` with
zero counts rather than reading the hash.

The decision includes `feature_count`, `basket_count`, `limited_by`, `ratio_over`,
and `retry_after_seconds`. Returned RPM counts include the proposed request;
returned TPM counts reflect already recorded usage. `ratio_over` is count divided
by the exceeded limit, so `1.1` means 110% of that limit. `retry_after_seconds` is
time until the next window; it is not currently used to reject or delay requests.

### After the upstream call

Both chat completion paths in [`completions.py`](../src/mlpa/core/completions.py)
and the search path in [`search.py`](../src/mlpa/core/search.py) schedule
`redis_service.update_contracts()` using `asyncio.create_task()` from their
`finally` blocks. It increments RPM by one and, if usage contains a positive
`total_tokens`, increments TPM by that amount. It does not estimate tokens or sum
`prompt_tokens` and `completion_tokens` when `total_tokens` is absent.

Updates are attempted for failures and disconnects that reach these finalization
blocks as well as successes. An early rejection before the completion function
is reached, such as a blocked user, does not schedule an update. Missing usage
still permits an RPM update.

The RPM and TPM increments run concurrently and are not one transaction. Each
individual increment atomically updates its feature and basket fields using Lua.

## Redis layout and lifecycle

[`RedisService`](../src/mlpa/core/services/redis_service.py) uses the Redis instance
selected by `REDIS_HOST` and `REDIS_PORT`, intended to be shared with LiteLLM. The
dedicated key prefix separates MLPA contract counters from other uses. MLPA connects
and pings Redis during application startup only when tracking is enabled, and
closes the connection during shutdown.

There is no periodic reset job. The service computes the bucket key from epoch
time on each check or increment:

```text
bucket_start = floor(epoch_seconds / window_seconds) * window_seconds
key = {TRAFFIC_CONTRACT_REDIS_KEY_PREFIX}:{rpm|tpm}:{bucket_start}

mlpa:traffic_contract:rpm:1789057680
mlpa:traffic_contract:tpm:1789057680
```

Each key is a Redis hash. Feature names are fields, and `__basket__` holds the sum
of recorded increments across features for that dimension and bucket.
There is no model or user component in the key: these counters are shared across
models and users using the same Redis instance and prefix.

The read-only check script does not reserve capacity, create counters, or refresh
expiration. The increment script uses `HINCRBY` for the feature and basket, then
`EXPIRE` to refresh the key's TTL. Non-positive increments do not change counters
or refresh TTL.

When the next 60-second window starts, operations use a new key such as
`mlpa:traffic_contract:rpm:1789057740`. The previous key remains until its TTL
expires, without affecting the new window. With a 120-second TTL, it expires 120
seconds after its last positive increment, not necessarily 120 seconds after the
bucket started. Keeping TTL longer than the window also makes recent buckets
available for inspection.

Redis eviction policy is a shared operational consideration with LiteLLM. Evicting
an active contract key loses its counts; the next check sees missing fields as
zero. A history without evictions does not establish what the configured policy
will do under future memory pressure. This implementation does not change that
policy or verify live eviction history.

## Worked example

Assume both windows are 60 seconds, TTL is 120 seconds, and all checks and completed
updates below occur sequentially in the same bucket. Limits are:

- Smart Window: RPM `2`, TPM `5000`.
- Shared basket: RPM `4`, TPM `10000`.
- S2S and S2S Android feature limits: `0` (unlimited individually).
- `ai` and `memories` share the `smart-window` feature.

All rows represent chat completion requests, including requests with the
`memories`, `s2s`, and `s2s-android` service types.

| Request | Service type | Returned tokens | RPM mode at check | TPM mode at check | Basket RPM after update | Basket TPM after update |
| --- | --- | ---: | --- | --- | ---: | ---: |
| 1 | `ai` | 2000 | `normal` | `normal` | 1 | 2000 |
| 2 | `memories` | 3500 | `normal` | `normal` | 2 | 5500 |
| 3 | `ai` | 500 | `borrowed` | `borrowed` | 3 | 6000 |
| 4 | `s2s` | 4500 | `normal` | `normal` | 4 | 10500 |
| 5 | `s2s-android` | 1000 | `degraded` | `degraded` | 5 | 11500 |

Request 2 checks RPM at exactly the Smart Window limit and checks TPM against the
previous 2000 tokens. Its response then pushes Smart Window TPM to 5500. Request
3 observes that overage and also projects Smart Window RPM to 3, so both modes
are `borrowed` while the basket still has capacity.

Request 4 projects basket RPM to exactly 4 and checks existing basket TPM at 6000,
so both decisions are still `normal`. Its response pushes basket TPM to 10500.
Request 5 then projects basket RPM to 5 and observes basket TPM at 10500, so both
decisions are `degraded`. Basket overage takes precedence over feature overage.
All five requests continue despite these classifications.

The hashes after all five updates are:

| Hash field | RPM value | TPM value |
| --- | ---: | ---: |
| `smart-window` | 3 | 6000 |
| `s2s` | 1 | 4500 |
| `s2s-android` | 1 | 1000 |
| `__basket__` | 5 | 11500 |

## Metrics and why this complements Grafana alerts

[`instrument_requests_middleware()`](../src/mlpa/core/middleware/instrumentation.py)
reads the modes from `request.state` and adds them to `mlpa_requests_total`.
Disabled checks, unmapped service types, and requests that never reach the check
use `N/A`. Check errors that fail open also retain `N/A`.

These labels expose how much evaluated traffic is within contract, borrowing
feature capacity, or exceeding the shared basket. For example, this shows the
percentage of evaluated requests in each RPM mode over five minutes:

```promql
100 * sum by (traffic_contract_rpm_mode) (
  rate(mlpa_requests_total{traffic_contract_rpm_mode!="N/A"}[5m])
) / ignoring(traffic_contract_rpm_mode) group_left
sum(rate(mlpa_requests_total{traffic_contract_rpm_mode!="N/A"}[5m]))
```

Use the corresponding TPM label for token modes. These are classifications at
check time; they do not retroactively change when the request's token usage is
recorded. The Redis counter values themselves are not exported by this service
as Prometheus gauges.

Grafana alerts remain useful for sustained traffic or saturation. The contract
check additionally makes a feature-versus-basket decision available inside each
request. Today that supports observing the mode distribution and tuning contract
limits. Later, it could drive shorter token limits, fewer retries, different
routing, or wait/jitter behavior. None of those changes are currently applied.

## Failure behavior and current limitations

- **Check failures:** with fail-open enabled, log the Redis error and continue with
  `N/A` modes. With fail-open disabled, return HTTP 503. This is distinct from an
  over-contract decision, which never rejects a request today.
- **Startup failures:** enabling tracking requires a successful Redis connection
  and ping at startup. The request-time fail-open setting does not bypass startup
  connection errors.
- **Update failures:** log the error; fail-closed configuration re-raises inside
  the background task. It cannot turn the already progressing response into a
  rejection. One dimension can update successfully while the other fails.
- **Deferred accounting:** checks do not reserve capacity. Concurrent requests
  can observe the same counters, and long streams remain uncounted until
  finalization. This is not an atomic admission limit.
- **Window boundaries:** checks and updates select their keys independently using
  current time. A request checked in one minute may be counted in the next minute
  when its background update runs. RPM and TPM also select their update buckets
  independently.
- **Background delivery:** counter updates are not awaited by the response or
  persisted in a durable queue. Process termination can lose pending updates.
- **Coverage:** the basket reflects recorded chat and search updates, not all
  incoming HTTP requests. Search responses without `usage.total_tokens` contribute
  RPM only; their token usage is not estimated.

## Tests and implementation references

- [`test_redis_service.py`](../src/tests/unit/test_redis_service.py): fake-Redis
  coverage for checking decisions and updating RPM/TPM with or without usage.
- [`test_traffic_contract_enforcer.py`](../src/tests/unit/test_traffic_contract_enforcer.py):
  check arguments, request-state modes, disabled tracking, and Redis error policy.
- [`test_traffic_contracts.py`](../src/tests/integration/test_traffic_contracts.py):
  mocked integration coverage proving an over-contract chat request still proceeds.
- [`test_search.py`](../src/tests/unit/test_search.py): background accounting for
  search success and failure, optional token usage, and disabled tracking.
