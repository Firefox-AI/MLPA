from mlpa.run import app


def _header_parameter(path: str, name: str) -> dict:
    app.openapi_schema = None
    params = app.openapi()["paths"][path]["post"]["parameters"]
    return next(p for p in params if p["name"] == name)


def test_chat_service_type_header_docs_exclude_search_types():
    service_type = _header_parameter("/v1/chat/completions", "service-type")

    assert service_type["schema"] == {
        "type": "string",
        "enum": [
            "ai",
            "s2s",
            "s2s-android",
            "memories",
            "answer",
            "telemetry",
            "ai-dev",
            "memories-dev",
            "mochi-dev",
        ],
        "title": "Service-Type",
    }


def test_search_service_type_header_docs_are_search_only():
    service_type = _header_parameter("/v1/search", "service-type")

    assert service_type["schema"] == {
        "type": "string",
        "enum": ["search", "search-dev"],
        "default": "search",
        "title": "Service-Type",
    }
