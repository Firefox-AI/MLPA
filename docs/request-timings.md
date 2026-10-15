# Request timing spans

## Response format

Send `debug-timing: true` as a header to enable request timing tracking and response.
Timing data is returned only for HTTP 200 responses. Other status codes retain their original response body and headers; debug-enabled requests still log timings.
HTTP 200 JSON object responses include a top-level `spans` array. HTTP 200 SSE streams ending with `data: [DONE]` receive one additional `data: {"spans": [...]}`
event. Clients must continue reading after `[DONE]` to receive it.

Both payloads, and the structured request-completion log, contain the basic shape:

```json
{
  "spans": [
    {"name": "total", "start_ms": 0.0, "duration_ms": 641.3},
    {"name": "auth_check", "start_ms": 1.1, "duration_ms": 8.2},
    {"name": "db", "start_ms": 9.8, "duration_ms": 14.5},
    {"name": "upstream", "start_ms": 26.5, "duration_ms": 612.9}
  ]
}
```

- `name` identifies the operation; it is not a unique ID.
- `start_ms` is the offset from entry into the timing middleware.
- `duration_ms` is elapsed wall time measured using a monotonic clock.
- Values retain fractional milliseconds; they are not rounded to whole milliseconds.
- The total span comes first, followed by completed spans ordered by start offset.
- Repeated operations remain separate entries. Nested and concurrent spans can
  overlap, so their durations must not be added to infer total wall time.
- Spans that exit with an exception or cancellation still record elapsed time.

For a chosen profiler timeline anchor `T`, a marker starts at
`T + start_ms` and ends at `T + start_ms + duration_ms`. The offsets reconstruct
the server timeline but do not establish its absolute alignment with a client
profile. Network transit and time before entering the middleware remain outside
this measurement.

## Timing boundaries

Response payloads are snapshots taken before encoding and sending the debug
information. Their total span ends at that snapshot. The log total ends after the
final ASGI body send, or when processing exits on failure; this does not mean the
client has received the bytes.

`db` measures user resolution, including HTTP provisioning when a user is new.
`upstream` measures the LiteLLM round trip, not just model-provider execution.
For streaming it also includes chunk processing and forwarding waits.

### Pre-processing and authentication

Chat, search, and privacy-filter requests have these additional spans when
debug timing is enabled:

| Span | Boundary |
| --- | --- |
| `request_body_read` | Reading the request body at the route boundary, including upload waiting; cached body reads do not add spans. |
| `prompt_parsing` | JSON decoding of the request body, excluding body reading. This does not parse the semantic contents of prompts. |
| `context_checks` | Authorization context checks: model/service compatibility, country, virtual-key selection, and purpose validation. |
| `auth_check` | Credential verification using the selected authentication backend. |
| `request_preparation` | Constructing and sanitizing the outbound payload. Streaming chat also includes upstream authorization-header preparation. |

The middleware separately records `request_size_check`, measuring the size
check itself and excluding its downstream `call_next` wait.

Body reading and JSON parsing timings use `TimingRoute` and `TimingRequest`.
`register_middleware()` selects the route class for subsequently registered app
routes; included routers must select `TimingRoute` themselves (as the filter
router does). Requests without the debug header use the ordinary route request.

### Streaming and transport

| Span | Boundary |
| --- | --- |
| `upstream_handshake` | Starting the streaming HTTP call to LiteLLM through receipt of response headers, including connection/pool waits and request sending. Also recorded when stream initiation fails. |
| `upstream_first_chunk` | Starting that call through receipt of its first nonempty body chunk. Absent when no upstream chunk arrives. |
| `first_byte_returned` | Entry into the timing middleware through completion of the first nonempty response-body ASGI send. Error bodies also qualify; empty bodies and headers do not. |

`upstream_first_chunk` includes the handshake; both are nested within `upstream`.
The first chunk may contain SSE metadata rather than a content token. The
existing TTFT metric starts at entry into the streaming completion generator,
whereas `first_byte_returned` includes earlier middleware, parsing, auth, and DB
work. Neither proves when the client receives bytes.

`first_byte_returned` is recorded after sending, so it is available in completion
logs. A final SSE debug event also includes it if an earlier chunk has been sent.
JSON response snapshots, and SSE streams delivered in a single body message,
are prepared before that send and therefore cannot include this milestone.

These timings measure the MLPA-to-LiteLLM hop. They do not isolate pure network
transit from provider execution or instrument LiteLLM's provider calls. No
distributed trace propagation, tokenization instrumentation, or percentile
analysis is added.

## Adding instrumentation

Simply use `with measure("operation_name", request):` around the operation, including
awaited calls. Example:

```
with measure("auth_check", request):
    await authorize_request(request)
```
