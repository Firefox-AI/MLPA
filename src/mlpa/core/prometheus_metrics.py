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


metrics = PrometheusMetrics(
	in_progress_requests=Gauge(
		"in_progress_requests", "Number of requests currently in progress."
	),
	requests_total=Counter(
		"requests_total",
		"Total number of requests handled by the proxy.",
		["method", "endpoint"],
	),
	response_status_codes=Counter(
		"response_status_codes_total",
		"Total number of response status codes.",
		["status_code"],
	),
	request_latency=Histogram(
		"request_latency_seconds", "Request latency in seconds.", ["method", "endpoint"]
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
)
