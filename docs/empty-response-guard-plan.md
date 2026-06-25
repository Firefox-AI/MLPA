# MLPA Plan: Empty-Response Availability Guard

## Goal

When LiteLLM returns a **successful (HTTP 200) but empty** completion — `content`
is `null`/`""`, no tool calls, `finish_reason == "stop"` — MLPA currently records
it as `AvailabilityReason.VALID_RESPONSE`. It is not valid from the user's point
of view: the client got an answer with no text. This plan makes MLPA classify
that outcome as a new `AvailabilityReason.EMPTY_CONTENT` (a `FAILURE` outcome) and
log it, so empties show up in the availability metric and in logs instead of
hiding inside the success bucket.

`EMPTY_CONTENT` is **distinct** from the existing `EMPTY_RESPONSE`. They are
different incidents (see the streaming table below): `EMPTY_RESPONSE` =
upstream sent *nothing* (broken routing/gateway, surfaced as 502);
`EMPTY_CONTENT` = upstream sent a well-formed but text-empty completion (model
behavior, passed through). Keeping them apart matters for triage — one is an
infra fault, the other a model/prompt fault.

**Non-goals:** no auto-retry, no payload rewriting, no client-facing behavior
change. This is observability only.

## Background: the trigger

Observed with `gemini-3.1-flash-lite` on an agentic (ReAct-style) prompt that
describes browser tools in prose but passes **no** real `tools` declarations.
The model is a thinking model that returned `content: null` with
`finish_reason: "stop"`. The original suspicion was a "think → try to use a tool
→ no real tool → stop" loop, but note the usage on the failing response: 6
`text_tokens` and **no thinking/reasoning token count**. If thinking had consumed
the turn we'd expect a thoughts token count, so "thinking caused the empty" is
**not confirmed** by the evidence. Other models emit text for the same prompt.

### The suspected upstream lever is uncertain (verified facts, litellm v1.84.4 — prod)

`gemini-3.1-flash-lite` in `dataservices-infra/.../values-prod.yaml` is the only
flash-lite without `reasoning_effort: none`. But, confirmed against the deployed
litellm source (`vertex_and_google_ai_studio_gemini.py`):

- `_is_gemini_3_or_newer` matches any `"gemini-3"` string, so 3.1-flash-lite takes
  the **Gemini-3 path**: `reasoning_effort` → `_map_reasoning_effort_to_thinking_level`,
  not `_map_reasoning_effort_to_thinking_budget`.
- For a Gemini-3 flash model, **both `none` and `disable`** map to
  `{"thinkingLevel": "minimal", "includeThoughts": False}`. **No value yields
  `thinkingBudget: 0`** — the code comment states it: *"Gemini 3 cannot fully
  disable thinking."*
- The 2.5 siblings are *not* Gemini 3, so their `none` does hit
  `{"thinkingBudget": 0}` (genuinely off). The 3.1 entry can never match them.

So adding `reasoning_effort: none` to the 3.1 block lowers thinking from Google's
default level → `minimal` (the floor on this model) and hides thoughts. It is
**less** thinking, not off — and because the token evidence doesn't implicate
thinking, it is an **experiment, not a known fix**. Validate on stage by replaying
the offending prompt and checking `content`; if still null at `minimal`, the real
lever is passing real `tools` declarations, a prompt fix, or pinning to the
non-thinking 2.5-flash-lite sibling. Also add the missing `stream_timeout: 5` /
`timeout: 120` to that block for parity regardless.

This MLPA guard is the **permanent net**: it doesn't fix any single model, it makes
the empty-but-successful outcome *visible* for any model, now and future — and it's
how you'll tell whether the uncertain config change above actually moved the needle.

## Definition of "empty" (precise, to avoid false positives)

An empty response is **all three** of:

1. `content` is falsy (`None` or `""`), **and**
2. no `tool_calls` (a tool call with `null` content is a legitimate response), **and**
3. `finish_reason == "stop"`.

Condition 3 deliberately excludes other finish reasons, which are *different*
problems and out of scope for this label:

| `finish_reason` | Meaning | Treatment here |
|---|---|---|
| `stop` + empty content | The bug we target | → `EMPTY_CONTENT` |
| `length` | Truncated (hit `max_tokens`) | Leave as `VALID_RESPONSE` (own label later if needed) |
| `content_filter` | Safety block | Leave as `VALID_RESPONSE` (own label later if needed) |
| `tool_calls` | Tool call, content often null | Not empty — has tool calls |

Reasoning content note: Gemini "thinking" text streams as
`delta.reasoning_content`, not `delta.content`. A response that is *all* thinking
and no user-facing text therefore has falsy `content` and is correctly flagged.
That is the intended signal, not a false positive.

**Single source of truth.** Both the stream and non-stream paths call one helper
so the definition can never drift between them:

```python
def is_empty_completion(content, tool_calls, finish_reason) -> bool:
    """A successful-but-text-empty completion (model returned no usable text)."""
    return not content and not tool_calls and finish_reason == "stop"
```

Place it in `completions.py` (module-level, near the other helpers).

## Two distinct "empty" cases in streaming — keep them separate

Streaming already has an empty path. Do **not** merge the new one into it:

| Case | Detection | Client sees | Availability |
|---|---|---|---|
| **No chunks at all** (existing) | `not streaming_started` (`completions.py:274`) | A 502 SSE error is yielded | `EMPTY_RESPONSE` (unchanged) |
| **Chunks arrived, no text** (new) | stream completed, no content delta, `finish_reason == "stop"` | The (empty) upstream stream, unchanged | `EMPTY_CONTENT` (new) |

Different label, different client behavior, different root cause. The first is a
broken upstream (nothing came) and we surface a 502. The second is a
valid-but-empty response we pass through untouched and only *observe*. Both map to
the `FAILURE` outcome, so the success-rate denominator reconciles either way, but
the `reason` label lets you tell an infra fault from a model fault.

## Code changes

### 0. New availability reason (`src/mlpa/core/prometheus_metrics.py`)

Add one enum member and one outcome mapping next to the existing `EMPTY_RESPONSE`
(`:49`, `:69`):

```python
# in class AvailabilityReason (completion-stage block):
EMPTY_CONTENT = "empty_content"  # failure — 200 OK but no usable text

# in _AVAILABILITY_OUTCOME_BY_REASON:
AvailabilityReason.EMPTY_CONTENT: AvailabilityOutcome.FAILURE,
```

The existing `test_every_availability_reason_maps_to_an_outcome`
(`test_availability.py:20`) guards completeness — if you add the enum member but
forget the mapping, that test fails. No `PrometheusRejectionReason` counterpart is
needed (this is not a rejection).

### 1. Non-stream — `_get_completion` (`src/mlpa/core/completions.py`, ~line 401–414)

Replace the current tail (after `litellm_routing_snapshot` parse) with empty
detection. `record_completion_success` is still called (tokens were billed); only
the availability disposition branches.

```python
choice = (data.get("choices") or [{}])[0]
message = choice.get("message", {}) or {}
tool_calls = message.get("tool_calls") or []
content = message.get("content")
finish_reason = choice.get("finish_reason")

tool_names = extract_tool_names(tool_calls)
record_completion_success(
    authorized_chat_request,
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
    tool_names=tool_names,
    snapshot=litellm_routing_snapshot,
)
result = PrometheusResult.SUCCESS

if is_empty_completion(content, tool_calls, finish_reason):
    availability_reason = AvailabilityReason.EMPTY_CONTENT
    logger.warning(
        f"Empty completion from {authorized_chat_request.model} "
        f"(finish_reason={finish_reason}, "
        f"completion_tokens={completion_tokens})"
    )
else:
    availability_reason = AvailabilityReason.VALID_RESPONSE
return data
```

- `result` stays `SUCCESS` — the proxy call succeeded; latency/token metrics are
  unchanged. Availability is a separate axis and `EMPTY_CONTENT` → `FAILURE`
  (added in step 0, next to `EMPTY_RESPONSE` at `prometheus_metrics.py:69`).
- `return data` unchanged — clients that handle `null` keep working.
- `tool_calls` is read from `message` here (non-stream shape), reusing the value
  already extracted at line 401–402 (collapse the duplicate read).

### 2. Streaming — `_stream_completion` (`src/mlpa/core/completions.py`)

**State init** (near `tool_calls_accum` at ~line 121):

```python
tool_calls_accum: dict[int, dict] = {}
saw_content = False
last_finish_reason: str | None = None
```

**Per-line parse loop** (inside the existing `for line in chunk_str.split("\n")`
block, ~line 252, alongside the `usage` and `tool_calls` handling):

```python
choice = (data.get("choices") or [{}])[0]
if choice.get("delta", {}).get("content"):
    saw_content = True
fr = choice.get("finish_reason")
if fr:
    last_finish_reason = fr
```

The `(data.get("choices") or [{}])` form is deliberate: the `include_usage` final
chunk can carry `"choices": []`, and the existing `data.get("choices", [{}])[0]`
returns `[]` (not the default) for that, so `[0]` raises `IndexError` — which the
inner `except (JSONDecodeError, UnicodeDecodeError, KeyError)` does **not** catch.
Apply the same `or [{}]` guard to the existing `delta`/`tool_calls` reads at
`:252-256` while here. (`delta.content == ""` is falsy, so empty-string deltas and
role-only first chunks don't flip `saw_content`.)

**After the loop**, alongside the existing `not streaming_started` block
(~line 274) but as a separate branch on the success path (~line 287, before
`record_completion_success` sets `VALID_RESPONSE`). The same helper is reused —
note the stream has no single response object, so we pass the accumulated state
(`tool_calls_accum`, `last_finish_reason`) instead:

```python
record_completion_success(...)  # unchanged
result = PrometheusResult.SUCCESS
empty_content = (
    streaming_started
    and not saw_content
    and is_empty_completion(None, tool_calls_accum, last_finish_reason)
)
if empty_content:
    availability_reason = AvailabilityReason.EMPTY_CONTENT
    logger.warning(
        f"Empty stream from {authorized_chat_request.model} "
        f"(finish_reason={last_finish_reason}, "
        f"completion_tokens={completion_tokens})"
    )
else:
    availability_reason = AvailabilityReason.VALID_RESPONSE
```

(`is_empty_completion(None, ...)` covers the `not content` term; `saw_content`
is tracked separately because streamed text never lands in a single `content`
field. `tool_calls_accum` being empty satisfies `not tool_calls`.)

- The `not streaming_started` 502 path is untouched — it returns early before
  this branch.
- `ABORT` (client disconnect) returns early at line 271–272 and never reaches
  here, so a disconnect is never mislabeled as empty.
- No chunk is altered or withheld; the empty stream is forwarded as-is.

## Files touched

| File | Change |
|---|---|
| `src/mlpa/core/prometheus_metrics.py` | Add `EMPTY_CONTENT` reason + outcome mapping |
| `src/mlpa/core/completions.py` | `is_empty_completion` helper + detection in `_get_completion` + `_stream_completion` (and `or [{}]` hardening of the chunk reads) |
| `src/tests/unit/test_completions.py` | New tests (below) |

No change to `metrics.py` (`record_completion_success` / `extract_tool_names`
reused as-is).

## Edge cases / decisions

- **`finish_reason: "length"` with empty content** — not flagged. Truncation at
  `max_tokens` is a config/usage issue, not the think-then-stop bug. Could earn a
  `TRUNCATED` label later; out of scope.
- **Tool call with `content: null`** — not flagged (condition 2). This is the
  normal shape of a tool-calling turn.
- **All-thinking, no text** — flagged. Correct: user-facing content is empty.
- **Streaming where `finish_reason` never arrives** — not flagged
  (`last_finish_reason is None`). Conservative: we only assert "empty" when the
  model explicitly said it stopped cleanly.
- **`n > 1` (multiple choices)** — guard inspects `choices[0]` only, matching all
  existing parsing in this module. Multi-choice is not used by MLPA clients today.

## Test plan (`src/tests/unit/test_completions.py`)

Reuse existing helpers `_availability_count`, `_availability_total`,
`metrics_spy`, `_capture_logs`.

Non-stream:
- `test_get_completion_empty_content_records_empty_content_availability`
  — response with `message.content = None`, no `tool_calls`,
  `finish_reason = "stop"`. Assert: returns the body unchanged, `result` latency
  is `SUCCESS`, `_availability_count(..., FAILURE, EMPTY_CONTENT) == 1`,
  `_availability_total == 1`, a warning was logged.
- `test_get_completion_empty_content_with_tool_calls_is_valid`
  — `content = None` but `tool_calls` present → `VALID_RESPONSE`.
- `test_get_completion_empty_content_finish_length_is_valid`
  — `content = None`, `finish_reason = "length"` → `VALID_RESPONSE`.

Streaming:
- `test_stream_completion_empty_content_records_empty_content_availability`
  — chunks: a role-only delta, then a final chunk
  `{"choices":[{"delta":{},"finish_reason":"stop"}]}`, then a usage chunk that
  carries `"choices": []` (exercises the `or [{}]` guard), then `[DONE]`. Assert
  all chunks forwarded unchanged,
  `_availability_count(..., FAILURE, EMPTY_CONTENT) == 1`, warning logged.
- `test_stream_completion_success` (existing) — confirm it still records
  `VALID_RESPONSE` (its chunks carry no `finish_reason`, so the guard can't fire).
- `test_stream_completion_reasoning_only_is_empty`
  — deltas carry only `reasoning_content`, final chunk `finish_reason: "stop"` →
  `EMPTY_CONTENT`.

Regression: `test_stream_completion` no-chunks/abort tests must still hit their
existing labels (the 502 path and `CLIENT_DISCONNECT` are unchanged).

## Metrics / querying

No schema change. After deploy:

```promql
# empty-content completions by service_type and model
sum by (service_type, model) (
  rate(mlpa_chat_availability_total{reason="empty_content"}[1h])
)

# empty-content rate as a share of completions
sum by (model) (rate(mlpa_chat_availability_total{reason="empty_content"}[1h]))
/
sum by (model) (rate(mlpa_chat_availability_total{outcome="success"}[1h]))
```

Add a panel to the availability dashboard. Use it to see whether the
`reasoning_effort: none` config change moves `gemini-3.1-flash-lite` empties, and
to catch any other model that starts emptying out. `empty_content` (model fault)
and `empty_response` (infra fault, 502) are separate `reason` values — chart both.

## Rollout

1. Try the LiteLLM config change (`reasoning_effort: none` → `minimal` on
   `gemini-3.1-flash-lite`) on **stage**, replay the offending prompt, check
   `content`. This is an experiment — it lowers thinking to the model floor but
   cannot disable it, and thinking isn't confirmed as the cause. If empties
   persist, fall back to real `tools` declarations / prompt fix / model pin.
2. Land this guard (code + tests) — independent of the config change; safe to ship
   in either order, and it's what measures whether step 1 helped.
3. Add the Prometheus panel; watch the `empty_content` trend after both land.

## Risk

Low. The change only branches an in-memory metric label and adds a warning log;
it does not alter request/response bytes, status codes, token accounting, or any
existing early-return path. Worst case of a logic slip is a mislabeled
availability sample, not a failed or corrupted request.
