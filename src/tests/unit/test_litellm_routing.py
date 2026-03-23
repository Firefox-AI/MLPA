import httpx
import pytest

from mlpa.core.classes import LitellmBackend
from mlpa.core.config import (
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
)
from mlpa.core.litellm_routing import (
    normalize_litellm_api_base,
    parse_litellm_routing_headers,
)


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        (None, LitellmBackend.UNKNOWN),
        ("", LitellmBackend.UNKNOWN),
        ("   ", LitellmBackend.UNKNOWN),
        (
            "https://api.together.xyz/v1",
            LitellmBackend.TOGETHER,
        ),
        (
            "HTTPS://API.TOGETHER.XYZ/V1/chat",
            LitellmBackend.TOGETHER,
        ),
        (
            "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/endpoints/e:predict",
            LitellmBackend.VERTEX,
        ),
        (
            "https://example.com",
            LitellmBackend.UNKNOWN,
        ),
        ("not-a-url", LitellmBackend.UNKNOWN),
    ],
)
def test_normalize_litellm_api_base(api_base, expected):
    assert normalize_litellm_api_base(api_base) == expected


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
    assert snap.backend == LitellmBackend.TOGETHER
    assert snap.attempted_fallbacks == 1
    assert snap.attempted_retries == 2
    assert snap.response_duration_ms == 123.5
    assert snap.response_cost_usd == pytest.approx(0.00042)


def test_parse_litellm_routing_headers_missing():
    snap = parse_litellm_routing_headers(httpx.Headers({}))
    assert snap.backend == LitellmBackend.UNKNOWN
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
