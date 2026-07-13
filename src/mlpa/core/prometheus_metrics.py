from dataclasses import dataclass
from enum import StrEnum

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram


class PrometheusResult(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    ABORT = "abort"


class PrometheusRejectionReason(StrEnum):
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    SIGNUP_CAP_EXCEEDED = "signup_cap_exceeded"
    INVALID_MODEL_NAME = "invalid_model_name"
    INVALID_REQUEST = "invalid_request"


class AvailabilityOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    EXCLUDED = "excluded"
    ABORT = "abort"


class AvailabilityReason(StrEnum):
    # Strings shared with PrometheusRejectionReason are kept identical so the
    # two counters reconcile. Keep them in sync when a rejection reason is added.

    # --- pre-completion reasons (recorded in the auth dependency and route body) ---
    AUTH_REJECTED = "auth_rejected"  # excluded
    INVALID_AUTH_REQUEST = "invalid_auth_request"  # excluded
    INVALID_SERVICE_TYPE_FOR_MODEL = "invalid_service_type_for_model"  # excluded
    SIGNUP_CAP_EXCEEDED = "signup_cap_exceeded"  # excluded
    BLOCKED = "blocked"  # excluded
    PROVISIONING_FAILURE = "provisioning_failure"  # failure

    # Defined but not yet emitted: auth backends normalize system failures to 401,
    # making them indistinguishable from expected rejections. Capturing this
    # properly requires a follow-on change to the auth backends themselves.
    AUTH_SYSTEM_FAILURE = "auth_system_failure"  # failure

    # --- completion-stage reasons (recorded inside stream_completion / get_completion) ---
    VALID_RESPONSE = "valid_response"  # success
    UPSTREAM_ERROR = "upstream_error"  # failure
    EMPTY_RESPONSE = "empty_response"  # failure
    BUDGET_EXCEEDED = "budget_exceeded"  # excluded
    RATE_LIMITED_PLATFORM = "rate_limited_platform"  # excluded
    RATE_LIMITED_UPSTREAM = "rate_limited_upstream"  # excluded
    PAYLOAD_TOO_LARGE = "payload_too_large"  # excluded
    INVALID_MODEL_NAME = "invalid_model_name"  # excluded
    INVALID_REQUEST = "invalid_request"  # excluded
    CLIENT_DISCONNECT = "client_disconnect"  # abort


_AVAILABILITY_OUTCOME_BY_REASON: dict[AvailabilityReason, AvailabilityOutcome] = {
    AvailabilityReason.AUTH_REJECTED: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.INVALID_AUTH_REQUEST: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.INVALID_SERVICE_TYPE_FOR_MODEL: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.SIGNUP_CAP_EXCEEDED: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.BLOCKED: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.PROVISIONING_FAILURE: AvailabilityOutcome.FAILURE,
    AvailabilityReason.AUTH_SYSTEM_FAILURE: AvailabilityOutcome.FAILURE,
    AvailabilityReason.VALID_RESPONSE: AvailabilityOutcome.SUCCESS,
    AvailabilityReason.UPSTREAM_ERROR: AvailabilityOutcome.FAILURE,
    AvailabilityReason.EMPTY_RESPONSE: AvailabilityOutcome.FAILURE,
    AvailabilityReason.BUDGET_EXCEEDED: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.RATE_LIMITED_PLATFORM: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.RATE_LIMITED_UPSTREAM: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.PAYLOAD_TOO_LARGE: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.INVALID_MODEL_NAME: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.INVALID_REQUEST: AvailabilityOutcome.EXCLUDED,
    AvailabilityReason.CLIENT_DISCONNECT: AvailabilityOutcome.ABORT,
}


def availability_outcome_for(reason: AvailabilityReason) -> AvailabilityOutcome:
    """Pure classifier: the availability outcome is fully determined by the reason."""
    return _AVAILABILITY_OUTCOME_BY_REASON[reason]


class TokenType(StrEnum):
    PROMPT = "prompt"
    COMPLETION = "completion"


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
BUCKETS_SEARCH = (
    0.1,
    0.2,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
    float("inf"),
)
BUCKETS_TTFT = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))
BUCKETS_TOOL_CALLS = (0, 1, 2, 3, 5, 10, 20, 50, float("inf"))
BUCKETS_TOKENS = (
    0,
    10,
    50,
    100,
    250,
    500,
    1000,
    2500,
    5000,
    10000,
    25000,
    float("inf"),
)
BUCKETS_LITELLM_ATTEMPTS = (0, 1, 2, 3, 5, float("inf"))


@dataclass
class PrometheusMetrics:
    # global request metrics
    in_progress_requests: Gauge
    requests_total: Counter
    requests_by_country_total: Counter
    response_status_codes: Counter
    request_latency: Histogram

    # auth
    validate_challenge_latency: Histogram
    validate_app_attest_latency: Histogram
    validate_app_assert_latency: Histogram
    validate_fxa_latency: Histogram
    validate_play_latency: Histogram
    validate_access_token_latency: Histogram
    fxa_verifications_total: Counter
    play_verifications_total: Counter
    access_token_verifications_total: Counter

    # chat completions
    chat_completion_latency: Histogram
    chat_completion_ttft: Histogram
    chat_tokens: Counter
    chat_tokens_per_request: Histogram
    chat_tool_calls: Counter
    chat_completions_with_tools: Counter
    chat_tool_calls_per_completion: Histogram
    chat_requests_with_tools: Counter
    chat_request_rejections: Counter
    chat_availability: Counter

    # search
    search_latency: Histogram
    search_request_rejections: Counter

    # litellm
    litellm_routed_completions: Counter
    litellm_attempted_fallbacks: Histogram
    litellm_attempted_retries: Histogram
    litellm_reported_duration_seconds: Histogram
    litellm_reported_cost_usd_total: Counter
    litellm_routed_tokens: Counter


def build_metrics(registry: CollectorRegistry = REGISTRY) -> PrometheusMetrics:
    """Construct a fresh `PrometheusMetrics` bound to `registry`.

    Production uses the default global registry; tests pass a per-test
    `CollectorRegistry` so they can assert on samples in isolation.
    """
    return PrometheusMetrics(
        in_progress_requests=Gauge(
            "mlpa_in_progress_requests",
            "Number of requests currently in progress.",
            registry=registry,
        ),
        requests_total=Counter(
            "mlpa_requests_total",
            "Total number of requests handled by the proxy.",
            ["method", "endpoint", "service_type", "purpose"],
            registry=registry,
        ),
        requests_by_country_total=Counter(
            "mlpa_requests_by_country_total",
            "Chat and search requests by client country (edge-derived), service_type, and model. "
            "Plain counter by design; country is kept off histograms to bound cardinality.",
            ["service_type", "model", "client_country"],
            registry=registry,
        ),
        response_status_codes=Counter(
            "mlpa_response_status_codes_total",
            "Total number of response status codes.",
            ["status_code"],
            registry=registry,
        ),
        request_latency=Histogram(
            "mlpa_request_latency_seconds",
            "Request latency in seconds.",
            ["method", "endpoint"],
            buckets=BUCKETS_REQUEST,
            registry=registry,
        ),
        validate_challenge_latency=Histogram(
            "mlpa_validate_challenge_latency_seconds",
            "Challenge validation latency in seconds.",
            ["result"],
            buckets=BUCKETS_FAST_AUTH,
            registry=registry,
        ),
        validate_app_attest_latency=Histogram(
            "mlpa_validate_app_attest_latency_seconds",
            "App Attest authentication latency in seconds.",
            ["result"],
            buckets=BUCKETS_AUTH,
            registry=registry,
        ),
        validate_app_assert_latency=Histogram(
            "mlpa_validate_app_assert_latency_seconds",
            "App Assert authentication latency in seconds.",
            ["result"],
            buckets=BUCKETS_AUTH,
            registry=registry,
        ),
        validate_fxa_latency=Histogram(
            "mlpa_validate_fxa_latency_seconds",
            "FxA authentication latency in seconds.",
            ["result", "verification_source"],
            buckets=BUCKETS_FXA,
            registry=registry,
        ),
        validate_play_latency=Histogram(
            "mlpa_validate_play_latency_seconds",
            "Play Integrity authentication latency in seconds.",
            ["result"],
            buckets=BUCKETS_AUTH,
            registry=registry,
        ),
        validate_access_token_latency=Histogram(
            "mlpa_validate_access_token_latency_seconds",
            "Access token authentication latency in seconds.",
            ["result"],
            buckets=BUCKETS_AUTH,
            registry=registry,
        ),
        fxa_verifications_total=Counter(
            "mlpa_fxa_verifications_total",
            "Total number of FxA token verifications.",
            ["verification_source"],
            registry=registry,
        ),
        play_verifications_total=Counter(
            "mlpa_play_verifications_total",
            "Total number of Play Integrity token verifications.",
            registry=registry,
        ),
        access_token_verifications_total=Counter(
            "mlpa_access_token_verifications_total",
            "Total number of Access token verifications.",
            registry=registry,
        ),
        chat_completion_latency=Histogram(
            "mlpa_chat_completion_latency_seconds",
            "Chat completion latency in seconds.",
            ["result", "model", "service_type", "purpose"],
            buckets=BUCKETS_COMPLETION,
            registry=registry,
        ),
        chat_completion_ttft=Histogram(
            "mlpa_chat_completion_ttft_seconds",
            "Time to first token for streaming chat completions in seconds.",
            ["model"],
            buckets=BUCKETS_TTFT,
            registry=registry,
        ),
        chat_tokens=Counter(
            "mlpa_chat_tokens",
            "Number of tokens for chat completions.",
            ["type", "model", "service_type", "purpose"],
            registry=registry,
        ),
        chat_tokens_per_request=Histogram(
            "mlpa_chat_tokens_per_request",
            "Distribution of tokens per chat completion request.",
            ["type", "model", "service_type", "purpose"],
            buckets=BUCKETS_TOKENS,
            registry=registry,
        ),
        chat_tool_calls=Counter(
            "mlpa_chat_tool_calls_total",
            "Total number of LLM tool invocations.",
            ["model", "service_type", "purpose"],
            registry=registry,
        ),
        chat_completions_with_tools=Counter(
            "mlpa_chat_completions_with_tools_total",
            "Number of completions that contained at least one tool call.",
            ["model", "service_type", "purpose"],
            registry=registry,
        ),
        chat_tool_calls_per_completion=Histogram(
            "mlpa_chat_tool_calls_per_completion",
            "Distribution of tool calls per completion.",
            ["model", "service_type", "purpose"],
            buckets=BUCKETS_TOOL_CALLS,
            registry=registry,
        ),
        chat_requests_with_tools=Counter(
            "mlpa_chat_requests_with_tools_total",
            "Number of chat requests that included a tools payload.",
            ["model", "service_type", "purpose"],
            registry=registry,
        ),
        chat_request_rejections=Counter(
            "mlpa_chat_request_rejections_total",
            "Number of chat requests rejected due to budget, rate limit, payload size, signup cap, invalid model name, or invalid request body.",
            ["reason", "model", "service_type", "purpose"],
            registry=registry,
        ),
        chat_availability=Counter(
            "mlpa_chat_availability_total",
            "Interim availability outcomes for chat completions. outcome is success/failure/excluded/abort; reason is the bounded cause. Availability = success / (success + failure).",
            ["outcome", "reason", "model", "service_type", "purpose"],
            registry=registry,
        ),
        search_latency=Histogram(
            "mlpa_search_latency_seconds",
            "Search latency in seconds.",
            ["result"],
            buckets=BUCKETS_SEARCH,
            registry=registry,
        ),
        search_request_rejections=Counter(
            "mlpa_search_request_rejections_total",
            "Number of search requests rejected due to budget, rate limit, payload size, signup cap, invalid model name, or invalid request body.",
            ["reason", "model", "service_type", "purpose"],
            registry=registry,
        ),
        litellm_routed_completions=Counter(
            "mlpa_litellm_routed_completions_total",
            "Successful chat completions with LiteLLM routing labels from response headers.",
            ["requested_model", "backend", "service_type", "purpose", "fallback_used"],
            registry=registry,
        ),
        litellm_attempted_fallbacks=Histogram(
            "mlpa_litellm_attempted_fallbacks",
            "LiteLLM-reported fallback attempts per successful completion (from x-litellm-attempted-fallbacks).",
            ["requested_model", "backend", "service_type", "purpose"],
            buckets=BUCKETS_LITELLM_ATTEMPTS,
            registry=registry,
        ),
        litellm_attempted_retries=Histogram(
            "mlpa_litellm_attempted_retries",
            "LiteLLM-reported retry attempts per successful completion (from x-litellm-attempted-retries).",
            ["requested_model", "backend", "service_type", "purpose"],
            buckets=BUCKETS_LITELLM_ATTEMPTS,
            registry=registry,
        ),
        litellm_reported_duration_seconds=Histogram(
            "mlpa_litellm_reported_duration_seconds",
            "LiteLLM proxy-reported request duration in seconds (x-litellm-response-duration-ms / 1000).",
            ["requested_model", "backend", "service_type", "purpose", "fallback_used"],
            buckets=BUCKETS_COMPLETION,
            registry=registry,
        ),
        litellm_reported_cost_usd_total=Counter(
            "mlpa_litellm_reported_cost_usd_total",
            "Cumulative LiteLLM-reported spend in USD (x-litellm-response-cost); use increase() over a range for windowed sums.",
            ["requested_model", "backend", "service_type", "purpose", "fallback_used"],
            registry=registry,
        ),
        litellm_routed_tokens=Counter(
            "mlpa_litellm_routed_tokens_total",
            "Token counts attributed to LiteLLM winning backend (from usage, same completion as routing headers).",
            [
                "type",
                "requested_model",
                "backend",
                "service_type",
                "purpose",
                "fallback_used",
            ],
            registry=registry,
        ),
    )


metrics = build_metrics()
