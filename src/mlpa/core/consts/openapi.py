from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import env

SEARCH_SERVICE_TYPES = ("search", "search-dev", "agent-search")
SEARCH_SERVICE_TYPES_SET = set(SEARCH_SERVICE_TYPES)

GUEST_DESCRIPTION = """
Guest tokens for Smart Window users without an account.

**Flow**

1. `GET /verify/v1/guest` with `Accept: application/json` returns
   `{"access_token": "...", "token_type": "Bearer"}`. If Fastly answers with a
   challenge instead, open the same URL in a browser tab and read the token
   from the `/verify/v1/guest/success#access_token=...` redirect.
2. Send the token as `Authorization: Bearer <token>` to `/v1/chat/completions`
   or `/v1/search`, with the usual `service-type`. MLPA maps it to the guest
   pool (`ai` → `ai-guest`) with its own budget. Guests can use `ai`,
   `memories`, `convo-starters`, `search`, `agent` and `agent-search`.
3. When the token expires, chat answers 401 `{"error": 17}`. Call
   `POST /verify/v1/guest/refresh` with the expired token as
   `Authorization: Bearer <token>`, store the new token and retry the request
   once. Refresh is a plain JSON call with no Fastly challenge or browser tab.
   It keeps the guest id (`sub`), so the guest keeps its budget and cap slot.
4. Refresh answers 401 `{"error": 16}` when the token is too old (see
   lifetimes below). Go back to step 1, this gives a new guest id with a new
   budget.

**Lifetimes** (defaults, each env sets its own in infra)

| Setting | Default | Meaning |
|---|---|---|
| `MLPA_GUEST_TOKEN_TTL_SECONDS` | 3600 (60 min) | Token valid for chat until `exp`. |
| `MLPA_GUEST_REFRESH_IDLE_SECONDS` | 63072000 (2 years) | How long a token can stay expired and still be refreshed. Each refresh restarts the clock, so an active guest never has to re-issue. |

Read `exp` from the JWT to refresh ahead of time, or just refresh on 401.
Don't hardcode the TTL, it differs per env.

**Client logic**

Numeric codes are in the top level `error` (`{"error": 17}`). Check them
first, the string messages are for humans and can change. "chat" below means both `/v1/chat/completions`
and `/v1/search`.

| Code | Status | Where | Meaning | Client action |
|---|---|---|---|---|
| 12 | 403 | chat, refresh | Guest cap is full and this guest has no slot. | Sign in with FxA. |
| 15 | 401 / 404 | chat, refresh (401), issue (404) | Guest access is off (kill switch). | Stop guest calls, sign in with FxA. Don't refresh or re-issue. |
| 17 | 401 | chat | Token expired. | Refresh (step 3) and retry once. |
| 16 | 401 | refresh | Token older than the idle timeout. | Issue a new token (step 1). |
| 14 | 400 | chat | This `service-type` isn't available for guests. | Don't call it as a guest. |
| 1 | 429 | chat | This guest's own budget is used up. | Wait `Retry-After`, or sign in. |
| 11 | 429 | chat | Shared budget for all guests is used up. | Wait `Retry-After`, or sign in. |
| 2 | 429 | chat | Rate limit (RPM/TPM). | Wait `Retry-After` (60s). |

Other errors without a numeric code:

| Status | Body | Where | Client action |
|---|---|---|---|
| 401 | `{"detail": "Invalid guest token"}` | chat | Bad signature or not a guest token. Issue a new token. |
| 401 | `{"detail": "Invalid guest token"}` | refresh | Not a guest token or bad signature. Issue a new token. |
| 503 | `{"error": "Guest access is not configured."}` | chat | Server config issue, not a client bug. Retry later. |
| 500 | | refresh | Server or DB problem. Retry later, keep the current token. |

With `"stream": true` the budget and rate limit errors arrive inside the
stream as `data: {"error": N}` instead of an HTTP status.

Guest cap slots are freed by infra cleanup after a token TTL without activity.
Refresh keeps the slot active, or claims a new one if it was freed (403 with
`{"error": 12}` if the cap is full by then).
"""

APP_DESCRIPTION = """
Authenticates and proxies LLM requests through LiteLLM to enact budgets and
per-user management.

## Error codes

Errors with a known cause carry a numeric code in the top level `error`
(`{"error": 1}`), the same for MLPA, Fastly and the request size check. Check
the code first, the string messages are for humans and can change. With `"stream": true`, errors that
happen after the stream started arrive inside it as `data: {"error": N}`.

| Code | Status | Source | Meaning | Client action |
|---|---|---|---|---|
| 1 | 429 | MLPA | User's own budget is used up. | Wait `Retry-After` (until the budget resets). |
| 2 | 429 | MLPA | User's rate limit (RPM/TPM). | Wait `Retry-After` (60s). |
| 3 | 413 | MLPA | Request body or context window too large. | Send a smaller request. |
| 4 | 403 | MLPA | Signed-in user cap is full, new user rejected. | Don't retry. |
| 5 | 429 | MLPA | Upstream model provider rate limit. | Wait `Retry-After` (60s). |
| 6 | 429 | Fastly | WAF rate limit at the edge. | Back off and retry later. |
| 7 | 406 | Fastly | Reserved for a Fastly block. | Don't retry. |
| 8 | 400 | MLPA | Unknown model name. | Fix the `model`. |
| 9 | 400 | MLPA | Malformed request body. | Fix the request. |
| 10 | 500 | MLPA | Global budget used up. | Wait `Retry-After` (300s). |
| 11 | 429 | MLPA | Shared budget for all guests is used up. | Wait `Retry-After`, or sign in with FxA. |
| 12 | 403 | MLPA | Guest cap is full and this guest has no slot. | Sign in with FxA. |
| 13 | 429 | MLPA | Traffic contract exceeded. | Wait `Retry-After` (next window). |
| 14 | 400 | MLPA | This `service-type` isn't available for this auth method. | Use another service type or auth method. |
| 15 | 401 / 404 | MLPA | Guest access is off (kill switch). | Stop guest calls, sign in with FxA. |
| 16 | 401 | MLPA | Guest token too old to refresh. | Get a new token with `GET /verify/v1/guest`. |
| 17 | 401 | MLPA | FxA or guest token expired. Only expiry, a bad token keeps its string error. | Refresh the token and retry once. Guests use `POST /verify/v1/guest/refresh`. |

See the Guest section for the full guest flow.
"""

TAGS_METADATA = [
    {"name": "Health", "description": "Health check endpoints."},
    {"name": "Metrics", "description": "Prometheus metrics endpoints."},
    {
        "name": "App Attest",
        "description": "iOS App Attest verification flow: (1) GET /verify/challenge to obtain a challenge, "
        "(2) POST /verify/attest with a JWT containing the attestation object. "
        "Use the attested key for subsequent requests to /v1/chat/completions with use-app-attest header.",
    },
    {
        "name": "Play Integrity",
        "description": "Endpoints for verifying Play Integrity payloads.",
    },
    {
        "name": "Guest",
        "description": GUEST_DESCRIPTION,
    },
    {"name": "LiteLLM", "description": "Endpoints for interacting with LiteLLM."},
    {"name": "Mock", "description": "Mock endpoints for testing purposes."},
    {
        "name": "User Management",
        "description": "Endpoints for managing user blocking status and budgets.",
    },
    {
        "name": "Privacy Filter",
        "description": "Endpoints for interacting with the Privacy Filter.",
    },
]

CHAT_COMPLETION_DESCRIPTION = """
Authorize first using App Attest, Play Integrity, FxA, or dev tier.

**Headers:**

- **Authorization** (required): Bearer token — FxA OAuth token, Play Integrity MLPA token, App Attest JWT, or guest token (see the Guest section).
- **service-type** (required): One of the keys in env.user_feature_budget — used for tracking and budget.
- **purpose** (required for ai/ai-dev/mochi-dev/memories/memories-dev): One of `chat`, `title-generation`, `convo-starters-sidebar` for AI; `memory-generation` for memories; omit for s2s.
- **x-dev-authorization** (required for ai-dev/memories-dev/mochi-dev): Experimentation token; also requires FxA in Authorization. Dev service types return 401 without it.
- **use-app-attest**: Set to `true` for iOS App Attest.
- **use-play-integrity**: Set to `true` for Android Play Integrity.
"""


SEARCH_DESCRIPTION = """
Web search proxied to Exa via LiteLLM. Authorize the same way as /v1/chat/completions.

**Headers:**

- **Authorization** (required): Bearer token — FxA OAuth token, Play Integrity MLPA token, App Attest JWT, or guest token (see the Guest section).
- **service-type**: `search` by default; use `search-dev` for experiments. Search has its own budget pool and no `purpose` header.
- **x-dev-authorization** (required for search-dev): Experimentation token; also requires FxA in Authorization.

**Required body fields:** `query` (string) and `max_results` (integer, 1-10).

**Optional Exa filters:**

- **includeDomains** (array of strings): Restrict results to the specified domains.
- **excludeDomains** (array of strings): Exclude results from the specified domains.
- **startPublishedDate** (string): Return results published after this ISO 8601 timestamp.
- **category** (string): Focus the search on a category, such as `news`.

Send filters at the top level of the JSON body. Omitted or null filters are not sent to Exa.

**Example:**

```json
{
  "query": "Firefox browser news",
  "max_results": 5,
  "includeDomains": ["mozilla.org"],
  "excludeDomains": ["example.org"],
  "startPublishedDate": "2026-01-01T00:00:00Z",
  "category": "news"
}
```
"""

# Success (200) response docs for the proxied LiteLLM endpoints. The chat endpoint
# returns either a JSON chat completion or an SSE stream depending on `stream`.
CHAT_COMPLETION_SUCCESS_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "OpenAI-compatible chat completion. Returns a JSON completion object, or "
            "a `text/event-stream` of SSE chunks when `stream` is `true`."
        ),
        "content": {
            "application/json": {},
            "text/event-stream": {},
        },
    }
}

SEARCH_SUCCESS_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Search results returned from the Exa search backend.",
        "content": {"application/json": {}},
    }
}


def customize_openapi(app: FastAPI, tags_metadata: list[dict]) -> None:
    """Add AttestationAuth and AssertionAuth schemas to OpenAPI docs."""

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=tags_metadata,
        )
        schemas = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
        attest_schema = AttestationAuth.model_json_schema()
        attest_schema["description"] = "JWT payload for POST /verify/attest"
        schemas["AttestationAuth"] = attest_schema
        assert_schema = AssertionAuth.model_json_schema()
        assert_schema["description"] = (
            "JWT payload for POST /v1/chat/completions with use-app-attest"
        )
        schemas["AssertionAuth"] = assert_schema

        paths = openapi_schema.setdefault("paths", {})
        post_chat = paths.get("/v1/chat/completions", {}).get("post", {})
        post_search = paths.get("/v1/search", {}).get("post", {})
        if post_chat:
            params = post_chat.setdefault("parameters", [])
            chat_service_types = [
                st
                for st in env.client_service_types
                if st not in SEARCH_SERVICE_TYPES_SET
            ]
            purpose_required = (
                [
                    st
                    for st in env.client_service_types
                    if env.service_type_requires_purpose(st) and "search" not in st
                ]
                if env.MLPA_REQUIRE_PURPOSE_HEADER
                else []
            )
            param_descriptions = {
                "service-type": "Service type for tracking and budget. Use ai-dev, memories-dev, or mochi-dev for experiments (higher limits).",
                "purpose": (
                    "Purpose for Prometheus and product tracking. "
                    + (
                        "Required for " + f"{', '.join(purpose_required)}. "
                        if purpose_required
                        else "Optional (validated if provided). "
                    )
                    + "AI: chat, title-generation, convo-starters-sidebar. "
                    + "Memories: memory-generation. Omit or leave empty for s2s, s2s-android."
                ),
                "x-dev-authorization": "Required for ai-dev/memories-dev/mochi-dev. Experimentation token; also requires Authorization (FxA). Without it, dev service types return 401.",
                "authorization": "Bearer token: FxA OAuth, Play Integrity MLPA token, or App Attest JWT.",
                "use-app-attest": "Optional. Set to true for iOS App Attest; Authorization must contain AssertionAuth JWT.",
                "use-qa-certificates": "Optional. For App Attest QA/sandbox testing.",
                "use-play-integrity": "Optional. Set to true for Android Play Integrity; Authorization contains MLPA token from /verify/play.",
            }
            for p in params:
                name = p.get("name")
                if name in param_descriptions:
                    p["description"] = param_descriptions[name]
                if name == "service-type":
                    p["schema"] = {
                        "type": "string",
                        "enum": chat_service_types,
                        "title": "Service-Type",
                    }

        if post_search:
            params = post_search.setdefault("parameters", [])
            for p in params:
                if p.get("name") == "service-type":
                    p["description"] = (
                        "Service type for search requests. Use search-dev for "
                        "experiments; it requires x-dev-authorization."
                    )
                    p["schema"] = {
                        "type": "string",
                        "enum": list(SEARCH_SERVICE_TYPES),
                        "default": "search",
                        "title": "Service-Type",
                    }
                elif p.get("name") == "x-dev-authorization":
                    p["description"] = (
                        "Required when service-type is search-dev. Experimentation "
                        "token; also requires Authorization (FxA)."
                    )

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = cast(Any, _openapi)
