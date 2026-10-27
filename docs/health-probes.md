# Health probes: liveness, readiness and dependencies

MLPA has three health endpoints, and they answer different questions:

- **Liveness** `GET /health/liveness` - is the process alive?
- **Readiness** `GET /health/readiness` - should this pod get traffic?
- **Dependencies** `GET /health/dependencies` - can MLPA reach everything it needs right now?

Liveness and readiness only look at the pod itself. Dependencies looks at the
shared services behind it. Keep them separate: every MLPA pod talks to the same
pgbouncer, Cloud SQL and LiteLLM, so when one of those is slow, a probe that checks
them fails on every pod at once. Kubernetes then pulls every pod out of rotation (or
restarts them all, for liveness), and a short slowdown becomes a full outage.

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

Readiness is ready once both DB pools have been created at startup. It makes no
network calls, so a slow database or LiteLLM can't take pods out of rotation.
Requests that hit a slow dependency fail on their own, with their own timeouts.

- **200** with `"status": "connected"` -> both pools are up
- **503** with `"status": "degraded"` -> a pool isn't set up (startup or shutdown)

```json
{
  "status": "connected",
  "mlpa_version": "1.2.3",
  "pg_server_dbs": {
    "postgres": "connected",
    "app_attest": "connected"
  }
}
```

Use it for the k8s `readinessProbe`.

## Dependencies

```bash
curl http://localhost:8080/health/dependencies
```

Dependencies runs the real checks. It passes only when all of them pass:

| Check | What it does | How "not ready" looks |
|-------|--------------|-----------------------|
| litellm PG pool | `SELECT 1` against the litellm DB | `pg_server_dbs.postgres: offline` |
| app_attest PG pool | `SELECT 1` against the app_attest DB | `pg_server_dbs.app_attest: offline` |
| LiteLLM | calls LiteLLM `/health/readiness`, expects db connected + healthy status | `litellm.status: unreachable` (or not healthy) |
| Privacy Filter (if enabled) | calls Privacy Filter `/readyz`, expects `ready: true` | `privacy_filter.status: unreachable` |

The checks run concurrently, so a slow one doesn't add to the others. A failed
check doesn't cancel the rest, the full result still comes back in the body.

- **200** with `"status": "connected"` -> everything passed
- **503** with `"status": "degraded"` -> something failed

Use it for the k8s `startupProbe`, so a new pod doesn't get traffic until it can
reach everything, and when debugging. Don't use it for readiness or liveness.

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
    "version": "1.40.0",
    "status": "connected",
    "db": "connected"
  },
  "privacy_filter": { "status": "disabled" }
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
    "version": "1.40.0",
    "status": "unreachable"
  },
  "privacy_filter": { "status": "disabled" }
}
```

### Timeouts

Every dependency check is bounded by `READINESS_CHECK_TIMEOUT_S` (default `2.0`
seconds), covering the DB queries and the HTTP calls. If a dependency hangs, the
check fails fast and reports degraded instead of the probe timing out.

## Suggested k8s config

```yaml
startupProbe:
  httpGet:
    path: /health/dependencies
    port: 8080
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 30

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

Kubernetes holds off liveness and readiness until the startup probe passes, so
neither needs a long `initialDelaySeconds`. Keep `failureThreshold` on liveness
loose, so a short GC pause or event-loop hiccup doesn't restart a healthy pod.
