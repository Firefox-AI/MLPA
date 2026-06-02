from dataclasses import dataclass

import pytest
from prometheus_client import CollectorRegistry, Counter
from prometheus_client.samples import Sample

from mlpa.core.prometheus_metrics import PrometheusMetrics, build_metrics

# Every module that does `from mlpa.core.prometheus_metrics import metrics`
# binds its own local name. Patching only the source module leaves these stale.
_METRICS_REFS = (
    "mlpa.core.prometheus_metrics.metrics",
    "mlpa.core.metrics.metrics",
    "mlpa.core.utils.metrics",
    "mlpa.core.auth.fxa.metrics",
    "mlpa.core.routers.play.play.metrics",
    "mlpa.core.routers.appattest.appattest.metrics",
    "mlpa.core.middleware.instrumentation.metrics",
)


@dataclass
class MetricsSpy:
    """Real `PrometheusMetrics` on an isolated registry, with introspection helpers."""

    metrics: PrometheusMetrics
    registry: CollectorRegistry

    def touched(self) -> set[str]:
        """Names of metric attributes that recorded an interaction.

        A metric is touched if any sample has a non-empty label set
        (labeled metric saw a `.labels()` call) or a non-zero value
        (unlabeled metric got `.inc()`/`.set()`/`.observe()`).
        """
        names: set[str] = set()
        for field_name in self.metrics.__dataclass_fields__:
            metric = getattr(self.metrics, field_name)
            for collected in metric.collect():
                if any(_is_interaction(s) for s in collected.samples):
                    names.add(field_name)
                    break
        return names

    def assert_only(self, expected: set[str]) -> None:
        """Assert the touched set matches `expected` exactly."""
        actual = self.touched()
        unexpected = actual - expected
        missing = expected - actual
        if unexpected or missing:
            parts = []
            if unexpected:
                parts.append(f"unexpected metrics touched: {sorted(unexpected)}")
            if missing:
                parts.append(f"expected metrics not touched: {sorted(missing)}")
            raise AssertionError("; ".join(parts))

    def value(self, attr: str, **labels: str) -> float:
        """Counter or Gauge sample value for the given label set (0 if not yet observed)."""
        return self._sample(
            attr,
            "_total" if isinstance(getattr(self.metrics, attr), Counter) else "",
            labels,
        )

    def histogram_count(self, attr: str, **labels: str) -> float:
        """Number of observations recorded into a Histogram for the given label set."""
        return self._sample(attr, "_count", labels)

    def histogram_sum(self, attr: str, **labels: str) -> float:
        """Sum of observations recorded into a Histogram for the given label set."""
        return self._sample(attr, "_sum", labels)

    def samples(self, attr: str) -> list[Sample]:
        """All samples for one metric attribute (across label combinations)."""
        metric = getattr(self.metrics, attr)
        out: list[Sample] = []
        for collected in metric.collect():
            out.extend(collected.samples)
        return out

    def _sample(self, attr: str, suffix: str, labels: dict[str, str]) -> float:
        metric = getattr(self.metrics, attr)
        value = self.registry.get_sample_value(metric._name + suffix, labels or None)
        return value if value is not None else 0.0


def _is_interaction(sample: Sample) -> bool:
    # `_created` samples carry the metric's creation timestamp and are always
    # non-zero — ignore them. Same for `_gcreated`.
    if sample.name.endswith(("_created", "_gcreated")):
        return False
    return bool(sample.labels) or sample.value != 0


@pytest.fixture
def metrics_spy(mocker) -> MetricsSpy:
    """Per-test isolated `PrometheusMetrics` bound to a fresh registry.

    Patches every known import site of the singleton so production code paths
    write into the test registry. Use `.touched()` / `.assert_only()` to verify
    *exactly* which metrics were written (catches unexpected calls).
    """
    registry = CollectorRegistry()
    fresh = build_metrics(registry)
    for ref in _METRICS_REFS:
        mocker.patch(ref, fresh)
    return MetricsSpy(metrics=fresh, registry=registry)
