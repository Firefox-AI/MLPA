# Request timing spans

## Response format

Send the `debug-timing-key` header matching `MLPA_DEBUG_TIMING_KEY` to enable
request timing tracking and response. JSON object responses include a top-level `spans` array. Successful SSE
streams ending with `data: [DONE]` receive one additional `data: {"spans": [...]}`
event. Clients must continue reading after `[DONE]` to receive it.

Both payloads, and the structured request-completion log, contain the same shape:

```json
{
  "spans": [
    {"name": "total", "start_ms": 0.0, "duration_ms": 641.3},
    {"name": "auth", "start_ms": 1.1, "duration_ms": 8.2},
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

## Adding instrumentation

Simply use `with measure("operation_name", request):` around the operation, including
awaited calls. Example:

```
with measure("auth", request):
    await authorize_request(request)
````
