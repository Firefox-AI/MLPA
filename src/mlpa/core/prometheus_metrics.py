from dataclasses import dataclass
from enum import StrEnum

from prometheus_client import Counter, Gauge, Histogram


class PrometheusResult(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


BUCKETS_FAST_AUTH = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, float("inf"))
BUCKETS_AUTH = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, float("inf"))
BUCKETS_FXA = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf"))
BUCKETS_REQUEST = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    float("inf"),
)
BUCKETS_COMPLETION = (
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    180.0,
    float("inf"),
)
BUCKETS_TTFT = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))


@dataclass
class PrometheusMetrics:
    in_progress_requests: Gauge
    requests_total: Counter
    response_status_codes: Counter
    request_latency: Histogram
    validate_challenge_latency: Histogram
    validate_app_attest_latency: Histogram
    validate_app_assert_latency: Histogram
    validate_fxa_latency: Histogram
    chat_completion_latency: Histogram
    chat_completion_ttft: Histogram
    chat_tokens: Counter


metrics = PrometheusMetrics(
    in_progress_requests=Gauge(
        "mlpa_in_progress_requests", "Number of requests currently in progress."
    ),
    requests_total=Counter(
        "mlpa_requests_total",
        "Total number of requests handled by the proxy.",
        ["method", "endpoint", "service_type"],
    ),
    response_status_codes=Counter(
        "mlpa_response_status_codes_total",
        "Total number of response status codes.",
        ["status_code"],
    ),
    request_latency=Histogram(
        "mlpa_request_latency_seconds",
        "Request latency in seconds.",
        ["method", "endpoint"],
        buckets=BUCKETS_REQUEST,
    ),
    validate_challenge_latency=Histogram(
        "mlpa_validate_challenge_latency_seconds",
        "Challenge validation latency in seconds.",
        ["result"],
        buckets=BUCKETS_FAST_AUTH,
    ),
    validate_app_attest_latency=Histogram(
        "mlpa_validate_app_attest_latency_seconds",
        "App Attest authentication latency in seconds.",
        ["result"],
        buckets=BUCKETS_AUTH,
    ),
    validate_app_assert_latency=Histogram(
        "mlpa_validate_app_assert_latency_seconds",
        "App Assert authentication latency in seconds.",
        ["result"],
        buckets=BUCKETS_AUTH,
    ),
    validate_fxa_latency=Histogram(
        "mlpa_validate_fxa_latency_seconds",
        "FxA authentication latency in seconds.",
        ["result"],
        buckets=BUCKETS_FXA,
    ),
    chat_completion_latency=Histogram(
        "mlpa_chat_completion_latency_seconds",
        "Chat completion latency in seconds.",
        ["result", "model", "service_type"],
        buckets=BUCKETS_COMPLETION,
    ),
    chat_completion_ttft=Histogram(
        "mlpa_chat_completion_ttft_seconds",
        "Time to first token for streaming chat completions in seconds.",
        ["model"],
        buckets=BUCKETS_TTFT,
    ),
    chat_tokens=Counter(
        "mlpa_chat_tokens",
        "Number of tokens for chat completions.",
        ["type", "model"],
    ),
)
