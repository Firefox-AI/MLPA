# Prometheus metric cardinality: how to check headroom before adding a label

Every new label on a metric multiplies its series count. Get this wrong and a
"small" Grafana filter either blows up storage/query cost or silently gets
dropped by a cardinality limit. This is the checklist to run through before
adding a label to an existing MLPA metric. Written up after
[AIPLAT-1266](https://mozilla-hub.atlassian.net/browse/AIPLAT-1266), adding a
country/region filter to Grafana, needed exactly this analysis.

## The formula

```
series = (product of each label's distinct values) × (bucket count, for Histograms only)
```

Counters and Gauges: one series per label combination. Histograms: one
series per label combination *per bucket* (`_bucket` samples), plus `_count`
and `_sum`. A histogram with 12 buckets costs about 12x what a counter with
the same labels costs.

Multiplying declared label sets together gives you a ceiling, not reality.
Labels usually correlate. MLPA's `purpose` header is only non-empty for 5 of
13 service types, so `service_type × purpose` never hits its full cross
product. Don't call something "too expensive" or "safe" until you've checked
the real numbers.

## How to check current headroom

MLPA metrics live in Google Managed Prometheus (GMP), queryable via its
PromQL-compatible API against `moz-fx-dataservices-high-{prod,nonpr}`. Auth
with local `gcloud` ADC (`gcloud auth login` if the token refresh fails), then
call the query API directly:
```bash
PROJECT="moz-fx-dataservices-high-prod"
TOKEN=$(gcloud auth print-access-token)
curl -s -G "https://monitoring.googleapis.com/v1/projects/${PROJECT}/location/global/prometheus/api/v1/query" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-urlencode 'query=<promql query>'
```

First, list a metric family's samples. GMP doesn't support `=~` on
`__name__`, so use its metadata endpoint filtered by prefix instead of a
regex query:
```bash
curl -s -G "https://monitoring.googleapis.com/v1/projects/${PROJECT}/location/global/prometheus/api/v1/label/__name__/values" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-urlencode 'match[]=mlpa_chat_completion_latency_seconds'
```

Then count current active series for one metric:
```
query=count(mlpa_chat_completion_latency_seconds_bucket)
```

Count distinct label combinations too. That's the number that matters before
you multiply by buckets:
```
query=count(mlpa_chat_completion_latency_seconds_count)
```

And check how many distinct values a candidate label would actually take in
production, using a metric that already carries it:
```
query=count(count by (client_country) (mlpa_requests_by_country_total))
```

That last one is the step people skip. A label that looks bounded in theory,
like an ISO country code, can still be wide open in practice. See below.

## Worked example: MLPA in prod

Snapshot pulled from prod while writing this doc. Numbers drift, rerun the
queries above for current ones.

| Metric | Distinct label combos | Buckets | Total series |
|---|---|---|---|
| `mlpa_chat_completion_latency_seconds` (result×model×service_type×purpose) | 128 | 11 | 1,408 |
| `mlpa_chat_completion_ttft_seconds` (model only) | 24 | 9 | 216 |
| `mlpa_chat_availability_total` (outcome×reason×model×service_type×purpose) | 187 | n/a | 187 |
| `mlpa_requests_by_country_total` (service_type×model×client_country) | 2,244 | n/a | 2,244 |

Two things stood out when pulling these. 211 distinct countries have shown up
in `client_country` on `requests_by_country_total`, out of the 249 in
`COUNTRY_CODES`. Country is a wide dimension in real traffic (bots, VPNs,
travelers), even though the app is officially available in only a handful of
markets. And only 6 distinct models are actively receiving traffic, out of
11 configured. Real variety runs lower than the declared label set, but not
always in the direction you'd guess.

Here's the number that matters for this decision: if `client_country` (full
ISO, 211 real values) had been added directly to `chat_completion_latency`,
its 1,408 series today would become roughly 297,000 (128 combos × 211
countries × 11 buckets). That's measured off real traffic, not a worst-case
guess, and it's why AIPLAT-1266 didn't go that route.

## Add a label to an existing metric, or make a new one?

Start with the metric type. Counters absorb a new bounded label fairly
cheaply, since there's no bucket multiplier. A histogram multiplies by
bucket count, so the same label costs `buckets`x more there than on a
counter with identical cardinality.

Next, check the label's real cardinality (the query above). If it's small,
roughly 10 or fewer distinct values, and closed, meaning you control it and
it's not user-generated freetext, adding it directly is fine.

If the label is wide, like full-ISO country, but the dashboard only needs a
handful of buckets, clamp it to a small hand-maintained set first (see the
pattern below), and only then decide whether it belongs on an existing
metric or a new one.

Two separate things push toward a dedicated, deliberately thin metric
instead of adding the label in place, and either one alone is enough:

- The existing combination count is already large (over roughly 100) and
  the new label would multiply it by another 5+ values.
- The dashboard use case doesn't need the new label correlated with the
  metric's existing labels at all. If it doesn't, drop those labels in the
  new metric rather than carrying them forward. Dropping an unneeded label
  usually saves more than the new label costs, even when the existing
  metric was cheap to begin with. `mlpa_chat_completion_ttft_seconds` has
  only 24 combos today (`model` alone), comfortably under the first
  trigger, but a country-only dashboard has no use for per-model TTFT.
  Adding `client_country` in place would take it from 216 series to 1,080
  (24 models × 5 countries × 9 buckets). Dropping `model` and building
  `chat_completion_ttft_by_country` with `client_country` alone costs 45
  series on top of the untouched original, 261 total, cheaper and simpler
  to query than the merged version.

Both `mlpa_requests_by_country_total` (AIPLAT-1020) and the three
AIPLAT-1266 by-country metrics (`chat_completion_latency_by_country`,
`chat_completion_ttft_by_country`, `chat_availability_by_country`) end up
carrying only `client_country` plus the bare minimum, not the full label set
of the metric they resemble, because the dashboard never needed that
intersection.

Some general Prometheus/GMP thresholds, not MLPA-specific: under about 10k
active series for a single metric, don't think about it further. Between 10k
and 100k, take a second look and check the real distinct-combo count before
adding anything else. And bot, scanner, or freetext input isn't label
material at all. Clamp it to a known set or drop it. An attacker-controlled
label value is a spoofing vector as much as a cost problem.

## The bounded-label pattern used in MLPA

Every label that comes from outside MLPA's own config, whether from a header
or an upstream response, goes through a `clamp_*` function in
`src/mlpa/core/utils.py` before it touches a metric. The raw value never
reaches a `.labels()` call directly.

Two flavors of this show up. Some labels are already a known, moderately
sized enum: `clamp_model`, `clamp_service_type`, `clamp_purpose` all bind to
`env.valid_*` config, falling back to `"invalid"` or `"other"`. Others are
externally supplied and technically unbounded, clamped to a purpose-built
small set instead: `clamp_country` bounds to the full ISO list, falling back
to `"unknown"`, while `clamp_launch_country` bounds to the 4-country
dashboard set, falling back to `"other"`. Two different clamps can exist for
the same raw header when different metrics need different bucketing
granularity. `clamp_country` backs the wide `requests_by_country_total`
counter, `clamp_launch_country` backs the thin by-country histograms, and
both read the same `X-Geo-Country` header.

When adding a new label, write the `clamp_*` function first. Decide its
bucket set explicitly instead of letting it default to whatever values show
up, then wire it into a metric.

## Checklist before shipping a new label

1. Which metric type, Counter or Histogram? Histograms cost more.
2. What's the label's real distinct-value count in production today? Measure
   it, don't estimate it.
3. Does it need a `clamp_*` function? Yes, unless the value already comes
   from a closed, MLPA-controlled enum.
4. Existing metric or new dedicated metric? Work through the decision above.
5. After shipping, re-measure with `count(<metric>)` against GMP and confirm
   it matches what you expected. This catches a clamp that isn't actually
   bounding what you thought it was.
