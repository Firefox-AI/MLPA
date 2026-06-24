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
| app_attest PG pool | reads the applied Alembic revision | `pg_server_dbs.app_attest: offline` |
| migrations | applied revisions match the heads the code ships | `migration.current` != `migration.expected` |
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
  "migration": {
    "expected": ["a1b2c3d4e5f6"],
    "current": ["a1b2c3d4e5f6"]
  },
  "litellm": {
    "litellm_version": "1.40.0",
    "status": "connected",
    "db": "connected"
  }
}
```

### Degraded response (migrations behind)

The DB is up, but the pod runs code that expects a newer migration than what's
applied. The pod could try to write rows the schema doesn't support yet, so it's
not ready.

```json
{
  "status": "degraded",
  "mlpa_version": "1.2.3",
  "pg_server_dbs": {
    "postgres": "connected",
    "app_attest": "connected"
  },
  "migration": {
    "expected": ["b2c3d4e5f6a7"],
    "current": ["a1b2c3d4e5f6"]
  },
  "litellm": {
    "litellm_version": "1.40.0",
    "status": "connected",
    "db": "connected"
  }
}
```

## The migration check

`migration.expected` is the Alembic head(s) baked into the running image, read from
the `alembic/` files. `migration.current` is what's applied on the app_attest DB.
If they don't match, the pod isn't ready.

Two cases:

- new code deployed before the migration ran -> `current` is behind `expected`
- old pod still running after a migration -> `current` is ahead of `expected`

Either way the pod and the schema disagree, so it stays out of rotation until they
match.

A fresh DB that was never migrated reads as `current: []`, which matches no real
head, so it also shows as not ready until you run the migrations.

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
