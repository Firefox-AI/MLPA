"""
Parse gateway response headers for routing / fallback observability.

LiteLLM's: https://docs.litellm.ai/docs/proxy/response_headers. Otari sends its
own (Otari-Provider, Otari-Attempt-Count, Otari-Fallback,
Otari-Response-Duration-Ms) and the cost in the body as usage.cost_usd.
"""

import math
from dataclasses import replace
from typing import Mapping

from mlpa.core.classes import LitellmRoutingSnapshot
from mlpa.core.config import (
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
    OTARI_HEADER_ATTEMPT_COUNT,
    OTARI_HEADER_FALLBACK,
    OTARI_HEADER_PROVIDER,
    OTARI_HEADER_RESPONSE_DURATION_MS,
)


def litellm_model_api_base_from_header(raw: str | None) -> str:
    """
    Value of x-litellm-model-api-base for metrics (verbatim aside from outer strip).
    Missing or blank -> "unknown".
    """
    if raw is None or not isinstance(raw, str):
        return "unknown"
    s = raw.strip()
    return s if s else "unknown"


def _safe_int_header(headers: Mapping[str, str], name: str) -> int:
    raw = headers.get(name)
    if raw is None:
        return 0
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        return 0


def _safe_float_header(headers: Mapping[str, str], name: str) -> float | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def parse_litellm_routing_headers(headers: Mapping[str, str]) -> LitellmRoutingSnapshot:
    """
    Build a snapshot from httpx response headers (case-insensitive keys via httpx).
    """
    if headers.get(OTARI_HEADER_PROVIDER) is not None:
        return _parse_otari_routing_headers(headers)
    api_base = headers.get(LITELLM_HEADER_MODEL_API_BASE)
    backend = litellm_model_api_base_from_header(api_base)
    fallbacks = _safe_int_header(headers, LITELLM_HEADER_ATTEMPTED_FALLBACKS)
    retries = _safe_int_header(headers, LITELLM_HEADER_ATTEMPTED_RETRIES)
    duration_ms = _safe_float_header(headers, LITELLM_HEADER_RESPONSE_DURATION_MS)
    cost = _safe_float_header(headers, LITELLM_HEADER_RESPONSE_COST)
    return LitellmRoutingSnapshot(
        backend=backend,
        attempted_fallbacks=fallbacks,
        attempted_retries=retries,
        response_duration_ms=duration_ms,
        response_cost_usd=cost,
    )


def _parse_otari_routing_headers(headers: Mapping[str, str]) -> LitellmRoutingSnapshot:
    """Otari names the instance that served the request and how many candidates it tried.

    Otari does not retry a candidate itself (provider SDK retries are not reported),
    so every attempt beyond the first is a fallback.
    """
    attempts = _safe_int_header(headers, OTARI_HEADER_ATTEMPT_COUNT)
    fell_back = (headers.get(OTARI_HEADER_FALLBACK) or "").strip().lower() == "true"
    return LitellmRoutingSnapshot(
        backend=litellm_model_api_base_from_header(headers.get(OTARI_HEADER_PROVIDER)),
        attempted_fallbacks=max(attempts - 1, 1 if fell_back else 0),
        attempted_retries=0,
        response_duration_ms=_safe_float_header(
            headers, OTARI_HEADER_RESPONSE_DURATION_MS
        ),
        response_cost_usd=None,
    )


def with_usage_cost(
    snapshot: LitellmRoutingSnapshot, usage: Mapping[str, object] | None
) -> LitellmRoutingSnapshot:
    """Fill the cost from Otari's usage.cost_usd when no header carried one."""
    if snapshot.response_cost_usd is not None or not isinstance(usage, dict):
        return snapshot
    cost = usage.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return snapshot
    if not math.isfinite(cost) or cost < 0:
        return snapshot
    return replace(snapshot, response_cost_usd=float(cost))
