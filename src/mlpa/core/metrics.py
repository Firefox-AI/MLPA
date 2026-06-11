from collections.abc import Iterable

from mlpa.core.classes import AuthorizedChatRequest, LitellmRoutingSnapshot
from mlpa.core.prometheus_metrics import (
    AvailabilityReason,
    PrometheusRejectionReason,
    PrometheusResult,
    TokenType,
    availability_outcome_for,
    metrics,
)


def _chat_labels(req: AuthorizedChatRequest) -> dict[str, str]:
    return {
        "model": req.model,
        "service_type": req.service_type,
        "purpose": req.purpose,
    }


def record_chat_request_rejection(
    req: AuthorizedChatRequest, reason: PrometheusRejectionReason
) -> None:
    metrics.chat_request_rejections.labels(reason=reason, **_chat_labels(req)).inc()


def record_chat_availability(
    req: AuthorizedChatRequest, reason: AvailabilityReason
) -> None:
    metrics.chat_availability.labels(
        outcome=availability_outcome_for(reason),
        reason=reason,
        **_chat_labels(req),
    ).inc()


def record_completion_latency(
    req: AuthorizedChatRequest,
    result: PrometheusResult,
    elapsed_seconds: float,
) -> None:
    metrics.chat_completion_latency.labels(result=result, **_chat_labels(req)).observe(
        elapsed_seconds
    )


def record_ttft(model: str, elapsed_seconds: float) -> None:
    metrics.chat_completion_ttft.labels(model=model).observe(elapsed_seconds)


def record_search_latency(result: PrometheusResult, elapsed_seconds: float) -> None:
    metrics.search_latency.labels(result=result).observe(elapsed_seconds)


def extract_tool_names(items: Iterable[dict]) -> list[str]:
    """Extract function names from OpenAI-format tool entries (request `tools` or response `tool_calls`)."""
    return [
        (t.get("function", {}).get("name") or "unknown")
        for t in items
        if isinstance(t, dict)
    ]


def record_request_with_tools(req: AuthorizedChatRequest) -> None:
    if not req.tools:
        return
    for name in extract_tool_names(req.tools):
        metrics.chat_requests_with_tools.labels(
            tool_name=name, **_chat_labels(req)
        ).inc()


def record_tool_metrics(req: AuthorizedChatRequest, tool_names: list[str]) -> None:
    n_calls = len(tool_names)
    labels_no_tool = _chat_labels(req)
    for name in tool_names:
        metrics.chat_tool_calls.labels(tool_name=name, **labels_no_tool).inc()
        metrics.chat_completions_with_tools.labels(
            tool_name=name, **labels_no_tool
        ).inc()
        metrics.chat_tool_calls_per_completion.labels(
            tool_name=name, **labels_no_tool
        ).observe(n_calls)


def _record_token_side(
    req: AuthorizedChatRequest,
    token_type: TokenType,
    count: int,
) -> None:
    labels = {"type": token_type, **_chat_labels(req)}
    metrics.chat_tokens.labels(**labels).inc(count)
    metrics.chat_tokens_per_request.labels(**labels).observe(count)


def record_litellm_routing_metrics(
    req: AuthorizedChatRequest,
    snapshot: LitellmRoutingSnapshot,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    fallback_used = "true" if snapshot.attempted_fallbacks > 0 else "false"
    labels_base = {
        "requested_model": req.model,
        "backend": snapshot.backend,
        "service_type": req.service_type,
        "purpose": req.purpose,
    }
    metrics.litellm_routed_completions.labels(
        **labels_base,
        fallback_used=fallback_used,
    ).inc()
    metrics.litellm_attempted_fallbacks.labels(**labels_base).observe(
        snapshot.attempted_fallbacks
    )
    metrics.litellm_attempted_retries.labels(**labels_base).observe(
        snapshot.attempted_retries
    )
    if snapshot.response_duration_ms is not None:
        metrics.litellm_reported_duration_seconds.labels(
            **labels_base,
            fallback_used=fallback_used,
        ).observe(snapshot.response_duration_ms / 1000.0)
    if snapshot.response_cost_usd is not None:
        metrics.litellm_reported_cost_usd_total.labels(
            **labels_base,
            fallback_used=fallback_used,
        ).inc(snapshot.response_cost_usd)
    for token_type, count in (
        (TokenType.PROMPT, prompt_tokens),
        (TokenType.COMPLETION, completion_tokens),
    ):
        if count > 0:
            metrics.litellm_routed_tokens.labels(
                type=token_type,
                **labels_base,
                fallback_used=fallback_used,
            ).inc(count)


def record_completion_success(
    req: AuthorizedChatRequest,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    tool_names: list[str],
    snapshot: LitellmRoutingSnapshot,
) -> None:
    for token_type, count in (
        (TokenType.PROMPT, prompt_tokens),
        (TokenType.COMPLETION, completion_tokens),
    ):
        if count > 0:
            _record_token_side(req, token_type, count)
    record_tool_metrics(req, tool_names)
    record_litellm_routing_metrics(req, snapshot, prompt_tokens, completion_tokens)
