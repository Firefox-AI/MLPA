import os
from unittest.mock import patch

import pytest

from mlpa.core.config import Env


def test_user_feature_budget_liner_answer_from_env():
    """Test that liner-answer budget configuration can be overridden via environment variables."""
    env_vars = {
        "USER_FEATURE_BUDGET_LINER_ANSWERS_BUDGET_ID": "custom-liner-budget-id",
        "USER_FEATURE_BUDGET_LINER_ANSWERS_MAX_BUDGET": "0.5",
        "USER_FEATURE_BUDGET_LINER_ANSWERS_RPM_LIMIT": "20",
        "USER_FEATURE_BUDGET_LINER_ANSWERS_TPM_LIMIT": "5000",
        "USER_FEATURE_BUDGET_LINER_ANSWERS_BUDGET_DURATION": "7d",
    }

    with patch.dict(os.environ, env_vars):
        env = Env()

        liner_config = env.service_type_config["liner-answer"]
        assert liner_config["budget_id"] == "custom-liner-budget-id"
        assert liner_config["max_budget"] == 0.5
        assert liner_config["rpm_limit"] == 20
        assert liner_config["tpm_limit"] == 5000
        assert liner_config["budget_duration"] == "7d"


def test_valid_major_fx_versions_set_uses_string_range():
    env = Env()

    assert "155" in env.valid_major_fx_versions_set
    assert 155 not in env.valid_major_fx_versions_set
    assert env.valid_major_fx_versions_set == {str(n) for n in range(100, 200)}


def test_valid_service_types_all_service_types():
    """Test that valid_service_types includes all configured service types."""
    env = Env()
    service_types = env.valid_service_types

    assert "ai" in service_types
    assert "s2s" in service_types
    assert "s2s-android" in service_types
    assert "memories" in service_types
    assert "search" in service_types
    assert "answer" in service_types
    assert "sw-answer" in service_types
    assert "liner-answer" in service_types
    assert "telemetry" in service_types
    assert "convo-starters" in service_types
    assert "agent" in service_types
    assert "agent-search" in service_types
    assert "widget-generation" in service_types
    assert "ai-dev" in service_types
    assert "memories-dev" in service_types
    assert "mochi-dev" in service_types
    assert "search-dev" in service_types
    assert "ai-guest" in service_types
    assert "memories-guest" in service_types
    assert "search-guest" in service_types
    assert "agent-guest" in service_types
    assert "agent-search-guest" in service_types
    assert len(service_types) == 24


def test_service_type_purposes():
    env = Env()
    purposes = env.service_type_purposes
    ai_purposes = [
        "chat",
        "title-generation",
        "convo-starters-sidebar",
        "smart-form-fill",
        "aitab",
        "auto-tab-grouping",
    ]
    assert purposes["ai"] == ai_purposes
    assert purposes["memories"] == ["memory-generation"]
    assert purposes["telemetry"] == ["chat"]
    assert purposes["convo-starters"] == ["convo-starters-sidebar"]
    assert purposes["ai-dev"] == ai_purposes
    assert purposes["mochi-dev"] == ai_purposes
    assert purposes["memories-dev"] == ["memory-generation"]


def test_service_type_purposes_s2s_empty():
    """Test service types that have no purpose."""
    env = Env()
    pass_butter = []
    purposes = env.service_type_purposes
    assert purposes["search-guest"] == pass_butter
    assert purposes["s2s"] == pass_butter
    assert purposes["s2s-android"] == pass_butter
    assert purposes["search"] == pass_butter
    assert purposes["answer"] == pass_butter
    assert purposes["sw-answer"] == pass_butter
    assert purposes["liner-answer"] == pass_butter
    assert purposes["widget-generation"] == pass_butter
    assert purposes["search-dev"] == pass_butter


def test_service_type_requires_purpose():
    """Test that purpose is required only for service types with configured allowlists."""
    env = Env()
    assert env.service_type_requires_purpose("ai") is True
    assert env.service_type_requires_purpose("memories") is True
    assert env.service_type_requires_purpose("s2s") is False
    assert env.service_type_requires_purpose("s2s-android") is False
    assert env.service_type_requires_purpose("search") is False
    assert env.service_type_requires_purpose("answer") is False
    assert env.service_type_requires_purpose("sw-answer") is False
    assert env.service_type_requires_purpose("liner-answer") is False
    assert env.service_type_requires_purpose("telemetry") is True
    assert env.service_type_requires_purpose("convo-starters") is True
    assert env.service_type_requires_purpose("ai-dev") is True
    assert env.service_type_requires_purpose("mochi-dev") is True
    assert env.service_type_requires_purpose("memories-dev") is True
    assert env.service_type_requires_purpose("search-dev") is False
    assert env.service_type_requires_purpose("agent") is True
    assert env.service_type_requires_purpose("agent-search") is True
    assert env.service_type_requires_purpose("widget-generation") is False


def test_valid_purposes_is_bounded_global_metric_allowlist():
    env = Env()

    assert "" in env.valid_purposes_set
    assert "chat" in env.valid_purposes_set
    assert "memory-generation" in env.valid_purposes_set
    assert "definitely-not-valid" not in env.valid_purposes_set


def test_valid_model_labels_are_explicit_metric_allowlist():
    env = Env()

    assert "openai/gpt-4o" in env.valid_model_labels
    assert "exa" in env.valid_model_labels
    assert "exa-search" in env.valid_model_labels
    assert "liner-answers" in env.valid_model_labels
    assert "not-a-configured-model" not in env.valid_model_labels


def test_service_type_config_values_are_defined():
    """Every service type must have complete feature, budget, and rate config."""
    env = Env()
    required_keys = {
        "feature",
        "budget_id",
        "budget_duration",
        "max_budget",
        "rpm_limit",
        "tpm_limit",
    }

    for service_type, config in env.service_type_config.items():
        assert set(config.keys()) == required_keys, (
            f"{service_type} service_type_config keys differ from required keys"
        )
        assert isinstance(config["feature"], str)
        assert config["feature"], f"{service_type} feature must be defined"
        assert isinstance(config["budget_id"], str)
        assert config["budget_id"], f"{service_type} budget_id must be defined"
        assert isinstance(config["budget_duration"], str)
        assert config["budget_duration"], (
            f"{service_type} budget_duration must be defined"
        )
        assert isinstance(config["max_budget"], float)
        assert config["max_budget"] >= 0
        assert isinstance(config["rpm_limit"], int)
        assert config["rpm_limit"] >= 0
        assert isinstance(config["tpm_limit"], int)
        assert config["tpm_limit"] >= 0


def test_traffic_contract_config_defined_for_each_service_type():
    env = Env()

    assert set(env.traffic_contract_config) == set(env.service_type_config)
    for service_type, contract in env.traffic_contract_config.items():
        service_config = env.service_type_config[service_type]
        assert contract["feature"] == service_config["feature"]
        assert isinstance(contract["rpm_limit"], int)
        assert contract["rpm_limit"] >= 0
        assert isinstance(contract["tpm_limit"], int)
        assert contract["tpm_limit"] >= 0


def test_valid_service_type_for_model_forced_pair():
    """Test that forced model/service-type pairs are enforced."""
    env = Env()

    assert env.valid_service_type_for_model("search", "exa-search") is True
    assert env.valid_service_type_for_model("search-dev", "exa-search") is True
    assert env.valid_service_type_for_model("answer", "exa-search") is False
    assert env.valid_service_type_for_model("answer", "exa") is True
    assert env.valid_service_type_for_model("sw-answer", "exa") is True
    assert env.valid_service_type_for_model("sw-answer", "exa-search") is False
    assert env.valid_service_type_for_model("ai", "exa") is False
    assert env.valid_service_type_for_model("search", "exa") is False
    assert env.valid_service_type_for_model("liner", "liner-answers") is True
    assert env.valid_service_type_for_model("answer", "liner-answers") is False
    assert env.valid_service_type_for_model("liner", "exa") is False
    assert env.valid_service_type_for_model("widget-generation", "deepseek-v4.1-flash")
    assert env.valid_service_type_for_model(
        "widget-generation", "deepseek-v4.1-flash-1017"
    )


def test_valid_service_type_for_model_unconfigured_model():
    """Test that unconfigured models reject service types forced to other models."""
    env = Env()

    assert env.valid_service_type_for_model("ai", "mistral-small-2603") is True
    assert env.valid_service_type_for_model("answer", "mistral-small-2603") is False
    assert env.valid_service_type_for_model("sw-answer", "mistral-small-2603") is False
    assert (
        env.valid_service_type_for_model("liner-answer", "mistral-small-2603") is False
    )
    assert env.valid_service_type_for_model("search", "mistral-small-2603") is False
    assert env.valid_service_type_for_model("search-dev", "mistral-small-2603") is False


def test_guest_enabled_refuses_default_signing_secret(monkeypatch):
    monkeypatch.setenv("MLPA_GUEST_ENABLED", "true")
    with pytest.raises(ValueError, match="MLPA_GUEST_TOKEN_SIGNING_SECRET"):
        Env()
    monkeypatch.setenv("MLPA_GUEST_TOKEN_SIGNING_SECRET", "real-secret")
    Env()


def test_search_guest_is_valid_for_exa_search():
    # /v1/search checks the service type after auth rewrites search -> search-guest
    assert Env().valid_service_type_for_model("search-guest", "exa-search")


def test_convo_starters_variants_mirror_base():
    env = Env()
    assert "convo-starters" in env.MLPA_CAPPED_SERVICE_TYPES
    assert "convo-starters-guest" in env.MLPA_GUEST_SERVICE_TYPES
    for st in ("convo-starters", "convo-starters-dev", "convo-starters-guest"):
        assert st in env.valid_service_types_set
        assert env.service_type_purposes[st] == ["convo-starters-sidebar"]
        assert env.service_type_requires_purpose(st) is True
    assert "convo-starters-guest" not in env.client_service_types
    # ai keeps the purpose so old Firefox clients keep working
    assert "convo-starters-sidebar" in env.service_type_purposes["ai"]


def test_agent_guest_mirrors_agent():
    env = Env()
    for st in ("agent", "agent-search"):
        guest = f"{st}-guest"
        assert guest in env.MLPA_GUEST_SERVICE_TYPES
        assert env.service_type_purposes[guest] == env.service_type_purposes[st]
    assert env.valid_service_type_for_model("agent-search-guest", "exa-search")
    assert not env.valid_service_type_for_model("agent-guest", "exa-search")


def test_unknown_litellm_timezone_fails_at_startup():
    with patch.dict(os.environ, {"LITELLM_TIMEZONE": "US/Easternn"}):
        with pytest.raises(ValueError, match="Unknown LITELLM_TIMEZONE"):
            Env()
