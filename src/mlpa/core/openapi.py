from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import env


def customize_openapi(app: FastAPI, tags_metadata: list[dict]) -> None:
    """Add AttestationAuth and AssertionAuth schemas to OpenAPI docs."""

    def _openapi():
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

        # Add header descriptions for POST /v1/chat/completions
        paths = openapi_schema.setdefault("paths", {})
        post_chat = paths.get("/v1/chat/completions", {}).get("post", {})
        if post_chat:
            params = post_chat.setdefault("parameters", [])
            purpose_required = [
                st
                for st in env.valid_service_types
                if env.service_type_requires_purpose(st)
            ]
            param_descriptions = {
                "service-type": "Service type for tracking and budget. Values: "
                f"{', '.join(env.valid_service_types)}. Use ai-dev, memories-dev, or mochi-dev for experiments (higher limits).",
                "purpose": "Purpose for Prometheus and product tracking. Required for "
                f"{', '.join(purpose_required)}. AI: chat, title-generation, convo-starters-sidebar. Memories: memory-generation. Omit or leave empty for s2s, s2s-android.",
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

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = _openapi
