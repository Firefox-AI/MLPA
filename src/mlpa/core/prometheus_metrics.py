from dataclasses import dataclass
from enum import Enum

from prometheus_client import Counter, Gauge, Histogram


class PrometheusResult(Enum):
    SUCCESS = "success"
    ERROR = "error"


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
    chat_completion_ttft: Histogram  # time to first token (when stream=True)
    chat_tokens: Counter

    # 3.3 Standard Service Metrics
    request_count_total: Counter
    request_duration_seconds: Histogram
    request_size_bytes: Histogram
    response_size_bytes: Histogram
    error_count_total: Counter

    # 4.1 Authorization Service Metrics
    auth_requests_total: Counter
    auth_request_duration_seconds: Histogram
    idp_request_duration_seconds: Histogram
    idp_request_errors_total: Counter
    auth_rate_limit_dropped_total: Counter
    auth_throttled_requests_total: Counter

    # 5.1 Routing Service Metrics
    router_requests_total: Counter
    router_decision_total: Counter
    router_request_duration_seconds: Histogram
    ai_backend_requests_total: Counter
    ai_backend_request_duration_seconds: Histogram
    ai_backend_timeouts_total: Counter
    ai_backend_retries_total: Counter
    ai_prompt_tokens_total: Counter
    ai_completion_tokens_total: Counter
    ai_total_tokens_total: Counter
    ai_cost_estimate_usd_total: Counter
    router_fallback_total: Counter

    # 6.1 AI Service Metrics
    inference_blocked_total: Counter


metrics = PrometheusMetrics(
    in_progress_requests=Gauge(
        "in_progress_requests", "Number of requests currently in progress."
    ),
    requests_total=Counter(
        "requests_total",
        "Total number of requests handled by the proxy.",
        ["method"],
    ),
    response_status_codes=Counter(
        "response_status_codes_total",
        "Total number of response status codes.",
        ["status_code"],
    ),
    request_latency=Histogram(
        "request_latency_seconds", "Request latency in seconds.", ["method"]
    ),
    validate_challenge_latency=Histogram(
        "validate_challenge_latency_seconds", "Challenge validation latency in seconds."
    ),
    validate_app_attest_latency=Histogram(
        "validate_app_attest_latency_seconds",
        "App Attest authentication latency in seconds.",
        ["result"],
    ),
    validate_app_assert_latency=Histogram(
        "validate_app_assert_latency_seconds",
        "App Assert authentication latency in seconds.",
        ["result"],
    ),
    validate_fxa_latency=Histogram(
        "validate_fxa_latency_seconds",
        "FxA authentication latency in seconds.",
        ["result"],
    ),
    chat_completion_latency=Histogram(
        "chat_completion_latency_seconds",
        "Chat completion latency in seconds.",
        ["result"],
    ),
    chat_completion_ttft=Histogram(
        "chat_completion_ttft_seconds",
        "Time to first token for streaming chat completions in seconds.",
    ),
    chat_tokens=Counter(
        "chat_tokens",
        "Number of tokens for chat completions.",
        ["type"],
    ),
    # 3.3 Standard Service Metrics
    request_count_total=Counter(
        "request_count_total", "Total number of requests received.", ["method"]
    ),
    request_duration_seconds=Histogram(
        "request_duration_seconds", "Request duration distribution.", ["method"]
    ),
    request_size_bytes=Histogram("request_size_bytes", "Size of requests in bytes."),
    response_size_bytes=Histogram("response_size_bytes", "Size of responses in bytes."),
    error_count_total=Counter(
        "error_count_total", "Total number of errors encountered.", ["error_type"]
    ),
    # 4.1 Authorization Service Metrics
    auth_requests_total=Counter(
        "auth_requests_total", "Total authorization requests.", ["result"]
    ),
    auth_request_duration_seconds=Histogram(
        "auth_request_duration_seconds",
        "Latency of authorization requests.",
        ["auth_method"],
    ),
    idp_request_duration_seconds=Histogram(
        "idp_request_duration_seconds",
        "Latency of requests to Identity Provider.",
        ["provider"],
    ),
    idp_request_errors_total=Counter(
        "idp_request_errors_total", "Total errors from Identity Provider.", ["provider"]
    ),
    auth_rate_limit_dropped_total=Counter(
        "auth_rate_limit_dropped_total",
        "Requests dropped due to rate limiting.",
    ),
    auth_throttled_requests_total=Counter(
        "auth_throttled_requests_total",
        "Requests throttled.",
    ),
    # 5.1 Routing Service Metrics
    router_requests_total=Counter(
        "router_requests_total", "Volume of routed requests.", ["model_name"]
    ),
    router_decision_total=Counter("router_decision_total", "Routing decisions made."),
    router_request_duration_seconds=Histogram(
        "router_request_duration_seconds", "Routing logic latency.", ["model_name"]
    ),
    ai_backend_requests_total=Counter(
        "ai_backend_requests_total", "Requests sent to AI backends.", ["model_name"]
    ),
    ai_backend_request_duration_seconds=Histogram(
        "ai_backend_request_duration_seconds",
        "Latency of AI backend requests.",
        ["model_name"],
    ),
    ai_backend_timeouts_total=Counter(
        "ai_backend_timeouts_total",
        "Total timeouts communicating with AI backends.",
        ["model_name"],
    ),
    ai_backend_retries_total=Counter(
        "ai_backend_retries_total", "Total retries to AI backends.", ["model_name"]
    ),
    ai_prompt_tokens_total=Counter(
        "ai_prompt_tokens_total", "Total prompt tokens consumed.", ["model_name"]
    ),
    ai_completion_tokens_total=Counter(
        "ai_completion_tokens_total",
        "Total completion tokens generated.",
        ["model_name"],
    ),
    ai_total_tokens_total=Counter(
        "ai_total_tokens_total", "Total tokens (prompt + completion).", ["model_name"]
    ),
    ai_cost_estimate_usd_total=Counter(
        "ai_cost_estimate_usd_total", "Estimated cost in USD.", ["model_name"]
    ),
    router_fallback_total=Counter(
        "router_fallback_total", "Total routing fallbacks triggered."
    ),
    # 6.1 AI Service Metrics
    inference_blocked_total=Counter(
        "inference_blocked_total", "Inferences blocked by safety filters.", ["backend"]
    ),
)
