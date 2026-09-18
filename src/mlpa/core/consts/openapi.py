from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import env

SEARCH_SERVICE_TYPES = ("search", "search-dev", "agent-search")
SEARCH_SERVICE_TYPES_SET = set(SEARCH_SERVICE_TYPES)

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

- **Authorization** (required): Bearer token — FxA OAuth token, Play Integrity MLPA token, or App Attest JWT.
- **service-type** (required): One of the keys in env.user_feature_budget — used for tracking and budget.
- **purpose** (required for ai/ai-dev/mochi-dev/memories/memories-dev): One of `chat`, `title-generation`, `convo-starters-sidebar` for AI; `memory-generation` for memories; omit for s2s.
- **x-dev-authorization** (required for ai-dev/memories-dev/mochi-dev): Experimentation token; also requires FxA in Authorization. Dev service types return 401 without it.
- **use-app-attest**: Set to `true` for iOS App Attest.
- **use-play-integrity**: Set to `true` for Android Play Integrity.
"""


SEARCH_DESCRIPTION = """
Web search proxied to Exa via LiteLLM. Authorize the same way as /v1/chat/completions.

**Headers:**

- **Authorization** (required): Bearer token — FxA OAuth token, Play Integrity MLPA token, or App Attest JWT.
- **service-type**: `search` by default; use `search-dev` for experiments. Search has its own budget pool and no `purpose` header.
- **x-dev-authorization** (required for search-dev): Experimentation token; also requires FxA in Authorization.

**Body:** `{"query": str, "max_results": int (1-10)}`.
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
                for st in env.valid_service_types
                if st not in SEARCH_SERVICE_TYPES_SET
            ]
            purpose_required = (
                [
                    st
                    for st in env.valid_service_types
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
