"""
Parse LiteLLM proxy response headers for routing / fallback observability.

See https://docs.litellm.ai/docs/proxy/response_headers
"""

import math
from typing import Mapping

from mlpa.core.classes import LitellmBackend, LitellmRoutingSnapshot
from mlpa.core.config import (
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
    env,
)


def _litellm_backend_for_id(backend_id: str) -> LitellmBackend:
    for m in LitellmBackend:
        if m.value == backend_id:
            return m
    return LitellmBackend.UNKNOWN


def normalize_litellm_api_base(api_base: str | None) -> LitellmBackend:
    """
    Map LiteLLM x-litellm-model-api-base to a low-cardinality backend label.
    """
    if not api_base or not isinstance(api_base, str):
        return LitellmBackend.UNKNOWN
    s = api_base.strip().lower()
    if not s:
        return LitellmBackend.UNKNOWN
    for backend_id, substrings in env.litellm_backend_api_base_matchers.items():
        for sub in substrings:
            if sub.lower() in s:
                return _litellm_backend_for_id(backend_id)
    return LitellmBackend.UNKNOWN


def _safe_int_header(headers: Mapping[str, str], name: str) -> int:
    raw = headers.get(name)
    if raw is None:
        return 0
    try:
        return int(float(str(raw).strip()))
    except (ValueError, TypeError):
        return 0


def _safe_float_header(headers: Mapping[str, str], name: str) -> float | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value):
        return None
    return value


def parse_litellm_routing_headers(headers: Mapping[str, str]) -> LitellmRoutingSnapshot:
    """
    Build a snapshot from httpx response headers (case-insensitive keys via httpx).
    """
    api_base = headers.get(LITELLM_HEADER_MODEL_API_BASE)
    backend = normalize_litellm_api_base(api_base)
    fallbacks = _safe_int_header(headers, LITELLM_HEADER_ATTEMPTED_FALLBACKS)
    retries = _safe_int_header(headers, LITELLM_HEADER_ATTEMPTED_RETRIES)
    duration_ms = _safe_float_header(headers, LITELLM_HEADER_RESPONSE_DURATION_MS)
    if duration_ms is not None and duration_ms < 0:
        duration_ms = None
    cost = _safe_float_header(headers, LITELLM_HEADER_RESPONSE_COST)
    if cost is not None and cost < 0:
        cost = None
    return LitellmRoutingSnapshot(
        backend=backend,
        attempted_fallbacks=fallbacks,
        attempted_retries=retries,
        response_duration_ms=duration_ms,
        response_cost_usd=cost,
    )
