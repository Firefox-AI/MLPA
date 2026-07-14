from collections.abc import Iterable

from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedSearchRequest,
    LitellmRoutingSnapshot,
)
from mlpa.core.config import env
from mlpa.core.prometheus_metrics import (
    AvailabilityReason,
    PrometheusRejectionReason,
    PrometheusResult,
    TokenType,
    availability_outcome_for,
    metrics,
)
from mlpa.core.utils import (
    clamp_country,
    clamp_model,
    clamp_purpose,
    clamp_service_type,
)

SEARCH_MODEL = "exa-search"


def _chat_labels(
    req: AuthorizedChatRequest, *, bounded_model: bool = False
) -> dict[str, str]:
    return {
        "model": clamp_model(req.model) if bounded_model else req.model,
        "service_type": clamp_service_type(req.service_type),
        "purpose": clamp_purpose(req.purpose),
    }


def _search_labels(req: AuthorizedSearchRequest) -> dict[str, str]:
    return {
        "model": SEARCH_MODEL,
        "service_type": clamp_service_type(req.service_type),
        "purpose": clamp_purpose(req.purpose),
    }


def record_request_country(
    raw_country: str | None, *, service_type: str, model: str
) -> None:
    metrics.requests_by_country_total.labels(
        service_type=clamp_service_type(service_type),
        model=clamp_model(model),
        client_country=clamp_country(raw_country),
    ).inc()


def record_chat_request_rejection(
    req: AuthorizedChatRequest, reason: PrometheusRejectionReason
) -> None:
    metrics.chat_request_rejections.labels(
        reason=reason, **_chat_labels(req, bounded_model=True)
    ).inc()


def record_search_request_rejection(
    req: AuthorizedSearchRequest, reason: PrometheusRejectionReason
) -> None:
    metrics.search_request_rejections.labels(reason=reason, **_search_labels(req)).inc()


def record_chat_availability_for(
    reason: AvailabilityReason,
    *,
    model: str,
    service_type: str,
    purpose: str,
) -> None:
    metrics.chat_availability.labels(
        outcome=availability_outcome_for(reason),
        reason=reason,
        model=clamp_model(model),
        service_type=clamp_service_type(service_type),
        purpose=clamp_purpose(purpose),
    ).inc()


def record_chat_availability(
    req: AuthorizedChatRequest, reason: AvailabilityReason
) -> None:
    record_chat_availability_for(
        reason,
        model=req.model,
        service_type=req.service_type,
        purpose=req.purpose,
    )


def record_completion_latency(
    req: AuthorizedChatRequest,
    result: PrometheusResult,
    elapsed_seconds: float,
) -> None:
    labels = _chat_labels(req, bounded_model=result != PrometheusResult.SUCCESS)
    metrics.chat_completion_latency.labels(result=result, **labels).observe(
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
    metrics.chat_requests_with_tools.labels(
        **_chat_labels(req, bounded_model=True)
    ).inc()


def record_tool_metrics(req: AuthorizedChatRequest, tool_names: list[str]) -> None:
    n_calls = len(tool_names)
    if n_calls == 0:
        return
    labels_no_tool = _chat_labels(req)
    metrics.chat_tool_calls.labels(**labels_no_tool).inc(n_calls)
    metrics.chat_completions_with_tools.labels(**labels_no_tool).inc()
    metrics.chat_tool_calls_per_completion.labels(**labels_no_tool).observe(n_calls)


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
        "service_type": clamp_service_type(req.service_type),
        "purpose": clamp_purpose(req.purpose),
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
