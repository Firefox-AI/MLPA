from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.metrics import (
    record_chat_availability,
    record_chat_request_rejection,
    record_completion_latency,
    record_request_country,
    record_request_with_tools,
    record_tool_metrics,
)
from mlpa.core.prometheus_metrics import (
    AvailabilityReason,
    PrometheusRejectionReason,
    PrometheusResult,
)
from mlpa.core.utils import INVALID_MODEL_LABEL, clamp_model


def _chat_request(model: str = "openai/gpt-4o") -> AuthorizedChatRequest:
    return AuthorizedChatRequest(
        user="test-user:ai",
        service_type="ai",
        purpose="chat",
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        tools=[
            {"type": "function", "function": {"name": "first_tool"}},
            {"type": "function", "function": {"name": "second_tool"}},
        ],
    )


def test_tool_metrics_are_aggregated_without_tool_name(metrics_spy):
    req = _chat_request()
    labels = {
        "model": req.model,
        "service_type": req.service_type,
        "purpose": req.purpose,
    }

    record_request_with_tools(req)
    record_tool_metrics(req, ["first_tool", "second_tool", "first_tool"])

    assert metrics_spy.value("chat_requests_with_tools", **labels) == 1
    assert metrics_spy.value("chat_tool_calls", **labels) == 3
    assert metrics_spy.value("chat_completions_with_tools", **labels) == 1
    assert metrics_spy.histogram_count("chat_tool_calls_per_completion", **labels) == 1
    assert metrics_spy.histogram_sum("chat_tool_calls_per_completion", **labels) == 3

    for metric_name in (
        "chat_requests_with_tools",
        "chat_tool_calls",
        "chat_completions_with_tools",
        "chat_tool_calls_per_completion",
    ):
        for sample in metrics_spy.samples(metric_name):
            assert "tool_name" not in sample.labels


def test_unknown_models_are_bucketed_on_failure_side_metrics(metrics_spy):
    req = _chat_request(model="unconfigured-model-from-request")
    labels = {
        "model": INVALID_MODEL_LABEL,
        "service_type": req.service_type,
        "purpose": req.purpose,
    }

    record_request_country("US", service_type=req.service_type, model=req.model)
    record_chat_request_rejection(req, PrometheusRejectionReason.INVALID_MODEL_NAME)
    record_chat_availability(req, AvailabilityReason.INVALID_MODEL_NAME)
    record_completion_latency(req, PrometheusResult.ERROR, 0.1)

    assert (
        metrics_spy.value(
            "requests_by_country_total",
            service_type=req.service_type,
            model=INVALID_MODEL_LABEL,
            client_country="US",
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "chat_request_rejections",
            reason=PrometheusRejectionReason.INVALID_MODEL_NAME,
            **labels,
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "chat_availability",
            outcome="excluded",
            reason=AvailabilityReason.INVALID_MODEL_NAME,
            **labels,
        )
        == 1
    )
    assert (
        metrics_spy.histogram_count(
            "chat_completion_latency",
            result=PrometheusResult.ERROR,
            **labels,
        )
        == 1
    )


def test_invalid_service_type_and_purpose_are_bucketed(metrics_spy):
    req = _chat_request(model="openai/gpt-4o")
    req.service_type = "not-a-service-type"
    req.purpose = "not-a-purpose"

    record_chat_request_rejection(req, PrometheusRejectionReason.INVALID_REQUEST)
    record_chat_availability(req, AvailabilityReason.INVALID_REQUEST)
    record_completion_latency(req, PrometheusResult.ERROR, 0.1)

    labels = {
        "model": req.model,
        "service_type": "other",
        "purpose": "other",
    }
    assert (
        metrics_spy.value(
            "chat_request_rejections",
            reason=PrometheusRejectionReason.INVALID_REQUEST,
            **labels,
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "chat_availability",
            outcome="excluded",
            reason=AvailabilityReason.INVALID_REQUEST,
            **labels,
        )
        == 1
    )
    assert (
        metrics_spy.histogram_count(
            "chat_completion_latency",
            result=PrometheusResult.ERROR,
            **labels,
        )
        == 1
    )


def test_configured_models_keep_their_metric_label(metrics_spy):
    req = _chat_request(model="openai/gpt-4o")

    assert clamp_model(req.model) == req.model

    record_request_country("DE", service_type=req.service_type, model=req.model)
    assert (
        metrics_spy.value(
            "requests_by_country_total",
            service_type=req.service_type,
            model=req.model,
            client_country="DE",
        )
        == 1
    )
