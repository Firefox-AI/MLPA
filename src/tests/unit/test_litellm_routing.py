import httpx
import pytest

from mlpa.core.config import (
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
)
from mlpa.core.litellm_routing import (
    litellm_model_api_base_from_header,
    parse_litellm_routing_headers,
    with_usage_cost,
)


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
        }
    )
    snap = parse_litellm_routing_headers(h)
    assert snap.backend == "https://api.together.xyz/v1"
    assert snap.attempted_fallbacks == 1
    assert snap.attempted_retries == 2
    assert snap.response_duration_ms == 123.5
    assert snap.response_cost_usd == pytest.approx(0.00042)


def test_parse_litellm_routing_headers_missing():
    snap = parse_litellm_routing_headers(httpx.Headers({}))
    assert snap.backend == "unknown"
    assert snap.attempted_fallbacks == 0
    assert snap.attempted_retries == 0
    assert snap.response_duration_ms is None
    assert snap.response_cost_usd is None


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


def test_parse_otari_routing_headers():
    snapshot = parse_litellm_routing_headers(
        httpx.Headers(
            {
                "Otari-Provider": "vertex_global",
                "Otari-Attempt-Count": "2",
                "Otari-Fallback": "true",
                "Otari-Response-Duration-Ms": "1899",
            }
        )
    )

    assert snapshot.backend == "vertex_global"
    assert snapshot.attempted_fallbacks == 1
    assert snapshot.attempted_retries == 0
    assert snapshot.response_duration_ms == 1899.0
    assert snapshot.response_cost_usd is None


def test_parse_otari_routing_headers_first_candidate():
    snapshot = parse_litellm_routing_headers(
        httpx.Headers(
            {
                "Otari-Provider": "vertex_ai",
                "Otari-Attempt-Count": "1",
                "Otari-Fallback": "false",
            }
        )
    )

    assert snapshot.backend == "vertex_ai"
    assert snapshot.attempted_fallbacks == 0


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"cost_usd": 0.005, "prompt_tokens": 0}, 0.005),
        ({"prompt_tokens": 3}, None),
        ({"cost_usd": -1.0}, None),
        ({"cost_usd": True}, None),
        (None, None),
    ],
)
def test_with_usage_cost_reads_otari_cost(usage, expected):
    snapshot = parse_litellm_routing_headers(
        httpx.Headers({"Otari-Provider": "exa_answers"})
    )

    assert with_usage_cost(snapshot, usage).response_cost_usd == expected


def test_with_usage_cost_keeps_a_header_cost():
    snapshot = parse_litellm_routing_headers(
        httpx.Headers({LITELLM_HEADER_RESPONSE_COST: "0.25"})
    )

    assert with_usage_cost(snapshot, {"cost_usd": 0.5}).response_cost_usd == 0.25
