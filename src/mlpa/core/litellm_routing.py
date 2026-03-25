"""
Parse LiteLLM proxy response headers for routing / fallback observability.

See https://docs.litellm.ai/docs/proxy/response_headers
"""

import math
from typing import Mapping

from mlpa.core.classes import LitellmRoutingSnapshot
from mlpa.core.config import (
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
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
    if not math.isfinite(value):
        return None
    return value


def parse_litellm_routing_headers(headers: Mapping[str, str]) -> LitellmRoutingSnapshot:
    """
    Build a snapshot from httpx response headers (case-insensitive keys via httpx).
    """
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
