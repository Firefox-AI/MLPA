from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from mlpa.core.classes import AssertionAuth, AttestationAuth


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
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = _openapi
