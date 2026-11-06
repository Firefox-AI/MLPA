# Guest kill switch

[AIPLAT-1450](https://mozilla-hub.atlassian.net/browse/AIPLAT-1450)

Two levers to stop guests fast. Start with lever 1. Use lever 2 when MLPA itself is the problem, or on top of lever 1 to be sure no guest spend gets through.

| | Stops new guest tokens | Stops guest spend | Time to effect | FxA / App Attest / Play users |
|---|---|---|---|---|
| 1. MLPA Redis flag | yes (404) | yes (401) | instant on the pod that got the call, ≤ `GUEST_KILL_SWITCH_CACHE_TTL_SECONDS` (5s) on the others | not affected |
| 2. LiteLLM `/key/block` on `MLPA_GUEST_VIRTUAL_KEY` | no | yes (401 from LiteLLM) | ~0.1s locally | not affected (other keys) |

`MLPA_GUEST_ENABLED=false` is still the hard off, it needs a deploy. The Redis flag can only turn guests off, `enable` does nothing while the env var is false.

## Lever 1: MLPA Redis flag

```bash
# disable
curl -X POST https://<mlpa-host>/user/guests/disable -H "master_key: Bearer $MASTER_KEY"
# {"guests_enabled": false}

# enable again
curl -X POST https://<mlpa-host>/user/guests/enable -H "master_key: Bearer $MASTER_KEY"
# {"guests_enabled": true}
```

It writes `mlpa:guests_enabled` = `0` / `1` in the MLPA Redis (same Memorystore as LiteLLM). Key absent means "follow `MLPA_GUEST_ENABLED`". Each pod caches the value for `GUEST_KILL_SWITCH_CACHE_TTL_SECONDS`.

Check it worked: `max(mlpa_guest_enabled)` in Grafana drops to 0 on every pod.

If Redis is down or slower than `GUEST_KILL_SWITCH_REDIS_TIMEOUT_SECONDS` (0.5s), MLPA keeps the last value it read (fail open, `GUEST_KILL_SWITCH_FAIL_OPEN_ON_REDIS_ERROR=true`) and logs `Guest kill switch read failed`. Set it to `false` to drop guests when Redis is down.

## Lever 2: block the guest key in LiteLLM

LiteLLM isn't public, go through the pod (workload `litellm` in `dataservices-infra/llm-proxy/k8s/llm-proxy/values-prod.yaml`, `kubectl -n llm-proxy-prod get deploy` for the exact name):

```bash
kubectl -n llm-proxy-prod port-forward deploy/<litellm-deployment> 4000:4000
curl -X POST http://localhost:4000/key/block \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"key": "<MLPA_GUEST_VIRTUAL_KEY>"}'
# undo
curl -X POST http://localhost:4000/key/unblock ... -d '{"key": "<MLPA_GUEST_VIRTUAL_KEY>"}'
```

Guests still get tokens (MLPA doesn't know), but every guest completion fails.

The guest gets a 401 with the generic upstream error. With `MLPA_DEBUG=true` (local only) it's LiteLLM's own text: `Authentication Error, Key is blocked...`.

## Local QA

```bash
docker compose -f litellm_docker_compose.yaml up -d
bash scripts/qa_guest_kill_switch.sh            # FXA_TOKEN=<token> to also check a real FxA user
```

Starts its own MLPA on port 8091 with exported env vars (doesn't touch `.env`) and checks both levers end to end, including time to effect and Redis down. It briefly stops the local `redis` container.
