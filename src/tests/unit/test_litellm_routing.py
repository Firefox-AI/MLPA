import httpx
import pytest

from mlpa.core.config import (
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_KEY_ALIAS,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
    LITELLM_HEADER_WORKLOAD,
)
from mlpa.core.litellm_routing import (
    litellm_key_alias_from_header,
    litellm_model_api_base_from_header,
    litellm_workload_from_header,
    parse_litellm_routing_headers,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "unknown"),
        ("", "unknown"),
        ("   ", "unknown"),
        ("evals-stage", "evals-stage"),
        ("evals%20%2F%20caf%C3%A9%20100%25", "evals / café 100%"),
        ("bad%0D%0Aheader", "unknown"),
        ("%FF", "unknown"),
        ("x" * 256, "unknown"),
        ("%61" * 256, "unknown"),
        ("\ud800", "unknown"),
    ],
)
def test_litellm_key_alias_from_header(raw, expected):
    assert litellm_key_alias_from_header(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "unknown"),
        ("", "unknown"),
        ("   ", "unknown"),
        ("eval", "eval"),
        ("load_test", "load_test"),
        ("evals-stage", "unknown"),
        ("eval-run-123", "unknown"),
        ("load test", "unknown"),
        ("EVAL", "unknown"),
        (" eval ", "unknown"),
        ([], "unknown"),
        ({}, "unknown"),
        ("%65val", "unknown"),
        ("bad%0D%0Aheader", "unknown"),
        ("%FF", "unknown"),
        ("x" * 256, "unknown"),
        ("%61" * 256, "unknown"),
        ("\ud800", "unknown"),
    ],
)
def test_litellm_workload_from_header(raw, expected):
    assert litellm_workload_from_header(raw) == expected


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        (None, "unknown"),
        ("", "unknown"),
        ("   ", "unknown"),
        ("https://api.together.xyz/v1", "https://api.together.xyz/v1"),
        (
            "HTTPS://API.TOGETHER.XYZ/V1/chat",
            "HTTPS://API.TOGETHER.XYZ/V1/chat",
        ),
        (
            "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/e:predict",
            "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/e:predict",
        ),
        ("https://example.com", "https://example.com"),
        ("not-a-url", "not-a-url"),
    ],
)
def test_litellm_model_api_base_from_header(api_base, expected):
    assert litellm_model_api_base_from_header(api_base) == expected


def test_parse_litellm_routing_headers_full():
    h = httpx.Headers(
        {
            LITELLM_HEADER_MODEL_API_BASE: "https://api.together.xyz/v1",
            LITELLM_HEADER_ATTEMPTED_FALLBACKS: "1",
            LITELLM_HEADER_ATTEMPTED_RETRIES: "2",
            LITELLM_HEADER_RESPONSE_DURATION_MS: "123.5",
            LITELLM_HEADER_RESPONSE_COST: "0.00042",
            LITELLM_HEADER_KEY_ALIAS: "eval-nightly",
            LITELLM_HEADER_WORKLOAD: "eval",
        }
    )
    snap = parse_litellm_routing_headers(h)
    assert snap.backend == "https://api.together.xyz/v1"
    assert snap.attempted_fallbacks == 1
    assert snap.attempted_retries == 2
    assert snap.response_duration_ms == 123.5
    assert snap.response_cost_usd == pytest.approx(0.00042)
    assert snap.key_alias == "eval-nightly"
    assert snap.workload == "eval"


def test_parse_litellm_routing_headers_missing():
    snap = parse_litellm_routing_headers(httpx.Headers({}))
    assert snap.backend == "unknown"
    assert snap.attempted_fallbacks == 0
    assert snap.attempted_retries == 0
    assert snap.response_duration_ms is None
    assert snap.response_cost_usd is None
    assert snap.key_alias == "unknown"
    assert snap.workload == "unknown"


def test_parse_litellm_routing_headers_invalid_numbers():
    h = httpx.Headers(
        {
            LITELLM_HEADER_ATTEMPTED_FALLBACKS: "not-a-number",
            LITELLM_HEADER_ATTEMPTED_RETRIES: "",
            LITELLM_HEADER_RESPONSE_DURATION_MS: "bad",
            LITELLM_HEADER_RESPONSE_COST: "nan",
        }
    )
    snap = parse_litellm_routing_headers(h)
    assert snap.attempted_fallbacks == 0
    assert snap.attempted_retries == 0
    assert snap.response_duration_ms is None
    assert snap.response_cost_usd is None


def test_parse_litellm_routing_headers_negative_cost():
    h = httpx.Headers({LITELLM_HEADER_RESPONSE_COST: "-1"})
    snap = parse_litellm_routing_headers(h)
    assert snap.response_cost_usd is None


def test_parse_litellm_routing_headers_negative_duration():
    h = httpx.Headers({LITELLM_HEADER_RESPONSE_DURATION_MS: "-5"})
    snap = parse_litellm_routing_headers(h)
    assert snap.response_duration_ms is None
