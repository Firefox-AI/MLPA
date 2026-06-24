# Health probes: liveness and readiness

MLPA has two health endpoints, and they answer different questions:

- **Liveness** `GET /health/liveness` - is the process alive?
- **Readiness** `GET /health/readiness` - can it actually serve traffic right now?

Liveness only looks at the process. Readiness looks at the dependencies MLPA needs
to serve requests. Keep them separate: if the liveness probe checks dependencies,
one slow database makes Kubernetes restart healthy pods and the outage gets worse.

## Liveness

```bash
curl http://localhost:8080/health/liveness
```

```json
{ "status": "alive" }
```

It doesn't touch the DB, LiteLLM, or any network call. If the event loop can answer,
the process is fine. Use it for the k8s `livenessProbe`. A failure means the pod is
stuck and k8s should restart it.

## Readiness

```bash
curl http://localhost:8080/health/readiness
```

Readiness runs the real checks. The pod is ready only when all of them pass:

| Check | What it does | How "not ready" looks |
|-------|--------------|-----------------------|
| litellm PG pool | `SELECT 1` against the litellm DB | `pg_server_dbs.postgres: offline` |
| app_attest PG pool | `SELECT 1` against the app_attest DB | `pg_server_dbs.app_attest: offline` |
| LiteLLM | calls LiteLLM `/health/readiness`, expects db connected + healthy status | `litellm.status: unreachable` (or not healthy) |

The checks run concurrently, so a slow one doesn't add to the others. A failed
check doesn't cancel the rest, the full result still comes back in the body.

### Status codes

- **200** with `"status": "connected"` -> everything passed, pod is ready
- **503** with `"status": "degraded"` -> something failed, keep the pod out of rotation

Use this for the k8s `readinessProbe`. On a 503 k8s stops routing traffic to the
pod but doesn't restart it. Once the dependency recovers, the next probe returns 200
and traffic comes back on its own.

### Healthy response

```json
{
  "status": "connected",
  "mlpa_version": "1.2.3",
  "pg_server_dbs": {
    "postgres": "connected",
    "app_attest": "connected"
  },
  "litellm": {
    "litellm_version": "1.40.0",
    "status": "connected",
    "db": "connected"
  }
}
```

### Degraded response (LiteLLM down)

The databases are reachable, but LiteLLM isn't answering, so MLPA can't proxy
completions. The checks are independent, so the body shows exactly which one failed.

```json
{
  "status": "degraded",
  "mlpa_version": "1.2.3",
  "pg_server_dbs": {
    "postgres": "connected",
    "app_attest": "connected"
  },
  "litellm": {
    "litellm_version": "1.40.0",
    "status": "unreachable"
  }
}
```

## Timeouts

Every readiness check is bounded by `READINESS_CHECK_TIMEOUT_S` (default `2.0`
seconds), covering the DB queries and the LiteLLM HTTP call. If a dependency hangs,
the check fails fast and reports degraded instead of the probe timing out. Bump the
env var if your probe interval needs more room.

## Suggested k8s config

```yaml
livenessProbe:
  httpGet:
    path: /health/liveness
    port: 8080
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health/readiness
    port: 8080
  periodSeconds: 10
  failureThreshold: 3
```

Keep `failureThreshold` on liveness loose, so a short GC pause or event-loop hiccup
doesn't restart a healthy pod. Readiness can be tighter, a false "not ready" only
costs a few seconds out of rotation, not a restart.
