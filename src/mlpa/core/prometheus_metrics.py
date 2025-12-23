from dataclasses import dataclass
from enum import Enum

from prometheus_client import Counter, Gauge, Histogram


class PrometheusResult(Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class PrometheusMetrics:
    in_progress_requests: Gauge

    # Standard
    request_count_total: Counter  # method(Get, Put, Update, Delete, )
    request_duration_seconds: Histogram  # method(Get, Put, Update, Delete, )
    request_size_bytes: Histogram  # method(Get, Put, Update, Delete, )
    response_size_bytes: Histogram
    response_status_codes: Counter  # method(Get, Put, Update, Delete, )
    request_error_count_total: Counter  # method, error_type

    # Auth
    auth_request_count_total: Counter
    auth_response_count_total: (
        Counter  # labels: method(fxa, appatest, ...), result (allow, deny, error)
    )
    auth_duration_seconds: Histogram  # labels: method(fxa, appatest, ...)
    auth_error_count_total: Counter  # labels: method(fxa, appatest, ...), error(rate limited, throttled, ...)

    # # Router Metrics
    # router_request_count_total: Counter  # labels: model_name
    # router_decision_count_total: Counter  # labels: decision_model_name,
    # router_request_duration_seconds: Histogram  # labels: model_name

    # AI Metrics
    ai_request_count_total: Counter  # labels: model_name
    ai_time_to_first_token: (
        Histogram  #  time to first token (when stream=True) labels: model_name
    )
    ai_request_duration_seconds: Histogram  # labels: model_name, streaming
    ai_error_count_total: (
        Counter  # labels: model_name, error(timeout, retry, blocked, ...)
    )
    ai_token_count_total: Counter  # labels: model_name, type


metrics = PrometheusMetrics(
    in_progress_requests=Gauge(
        "in_progress_requests", "Number of requests currently in progress."
    ),
    # Standard
    request_count_total=Counter(
        "request_count_total",
        "Total number of requests received handled by the proxy.",
        ["method"],
    ),
    request_duration_seconds=Histogram(
        "request_duration_seconds",
        "Total roundtrip request duration distribution.",
        ["method"],
    ),
    request_size_bytes=Histogram(
        "request_size_bytes", "Size of requests in bytes.", ["method"]
    ),
    response_size_bytes=Histogram(
        "response_size_bytes",
        "Size of responses in bytes.",
    ),
    response_status_codes=Counter(
        "response_status_codes_total",
        "Total number of response status codes.",
        ["status_code"],
    ),
    request_error_count_total=Counter(
        "request_error_count_total",
        "Total number of errors encountered.",
        ["method", "error_type"],
    ),
    # Auth
    auth_request_count_total=Counter(
        "auth_request_count_total",
        "Total authorization requests.",
    ),
    auth_response_count_total=Counter(
        "auth_response_count_total",
        "Total authorization responses.",
        ["method", "result"],
    ),
    auth_duration_seconds=Histogram(
        "auth_duration_seconds",
        "Latency of authorization requests.",
        ["method", "result"],
    ),
    auth_error_count_total=Counter(
        "auth_error_count_total", "Total authorization errors.", ["error"]
    ),
    # AI Metrics
    ai_request_count_total=Counter(
        "ai_request_count_total", "Total requests sent to AI backends.", ["model_name"]
    ),
    ai_time_to_first_token=Histogram(
        "ai_time_to_first_token_seconds",
        "Time to first token for streaming completions.",
        ["model_name"],
    ),
    ai_request_duration_seconds=Histogram(
        "ai_request_duration_seconds",
        "Latency of AI backend requests.",
        ["model_name", "streaming"],
    ),
    ai_error_count_total=Counter(
        "ai_error_count_total",
        "Total errors communicating with AI backends.",
        ["model_name", "error"],
    ),
    ai_token_count_total=Counter(
        "ai_token_count_total", "Total tokens consumed.", ["model_name", "type"]
    ),
)
