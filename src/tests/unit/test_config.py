import os
from unittest.mock import patch

import pytest

from mlpa.core.config import Env


def test_user_feature_budget_includes_memories():
    """Test that user_feature_budget property includes memories service type."""
    env = Env()
    budgets = env.user_feature_budget

    # Verify memories is present
    assert "memories" in budgets
    assert isinstance(budgets["memories"], dict)

    # Verify memories has all required keys
    memories_config = budgets["memories"]
    assert "budget_id" in memories_config
    assert "max_budget" in memories_config
    assert "rpm_limit" in memories_config
    assert "tpm_limit" in memories_config
    assert "budget_duration" in memories_config


def test_user_feature_budget_memories_default_values():
    """Test that memories budget configuration has correct default values."""
    env = Env()
    memories_config = env.user_feature_budget["memories"]

    assert memories_config["budget_id"] == "end-user-budget-memories"
    assert memories_config["max_budget"] == 0.1
    assert memories_config["rpm_limit"] == 10
    assert memories_config["tpm_limit"] == 2000
    assert memories_config["budget_duration"] == "1d"


def test_user_feature_budget_memories_from_env():
    """Test that memories budget configuration can be overridden via environment variables."""
    env_vars = {
        "USER_FEATURE_BUDGET_MEMORIES_BUDGET_ID": "custom-memories-budget-id",
        "USER_FEATURE_BUDGET_MEMORIES_MAX_BUDGET": "0.5",
        "USER_FEATURE_BUDGET_MEMORIES_RPM_LIMIT": "20",
        "USER_FEATURE_BUDGET_MEMORIES_TPM_LIMIT": "5000",
        "USER_FEATURE_BUDGET_MEMORIES_BUDGET_DURATION": "7d",
    }

    with patch.dict(os.environ, env_vars):
        env = Env()
        memories_config = env.user_feature_budget["memories"]

        assert memories_config["budget_id"] == "custom-memories-budget-id"
        assert memories_config["max_budget"] == 0.5
        assert memories_config["rpm_limit"] == 20
        assert memories_config["tpm_limit"] == 5000
        assert memories_config["budget_duration"] == "7d"


def test_valid_service_types_includes_memories():
    """Test that valid_service_types property includes memories."""
    env = Env()
    service_types = env.valid_service_types

    assert "memories" in service_types
    assert isinstance(service_types, list)


def test_user_feature_budget_dev_service_types_default_values():
    """Test that ai-dev, memories-dev, and mochi-dev have correct default values."""
    env = Env()
    ai_dev_config = env.user_feature_budget["ai-dev"]
    memories_dev_config = env.user_feature_budget["memories-dev"]
    mochi_dev_config = env.user_feature_budget["mochi-dev"]

    assert ai_dev_config["budget_id"] == "end-user-budget-ai-dev"
    assert ai_dev_config["max_budget"] == 1.0
    assert ai_dev_config["rpm_limit"] == 200
    assert ai_dev_config["tpm_limit"] == 10000

    assert memories_dev_config["budget_id"] == "end-user-budget-memories-dev"
    assert memories_dev_config["max_budget"] == 1.0
    assert memories_dev_config["rpm_limit"] == 50
    assert memories_dev_config["tpm_limit"] == 5000

    assert mochi_dev_config["budget_id"] == "end-user-budget-mochi-dev"
    assert mochi_dev_config["max_budget"] == 1.0
    assert mochi_dev_config["rpm_limit"] == 200
    assert mochi_dev_config["tpm_limit"] == 10000


def test_valid_service_types_all_service_types():
    """Test that valid_service_types includes all configured service types."""
    env = Env()
    service_types = env.valid_service_types

    assert "ai" in service_types
    assert "s2s" in service_types
    assert "s2s-android" in service_types
    assert "memories" in service_types
    assert "ai-dev" in service_types
    assert "memories-dev" in service_types
    assert "mochi-dev" in service_types
    assert "search" in service_types
    assert len(service_types) == 8


def test_service_type_purposes_ai_and_memories():
    """Test service_type_purposes: AI types have chat/title-generation/convo-starters-sidebar, memories have memory-generation."""
    env = Env()
    purposes = env.service_type_purposes
    ai_purposes = ["chat", "title-generation", "convo-starters-sidebar", "telemetry"]
    assert purposes["ai"] == ai_purposes
    assert purposes["ai-dev"] == ai_purposes
    assert purposes["mochi-dev"] == ai_purposes
    assert purposes["memories"] == ["memory-generation", "telemetry"]
    assert purposes["memories-dev"] == ["memory-generation", "telemetry"]


def test_service_type_purposes_s2s_empty():
    """Test that s2s, s2s-android, and search have no purposes (empty list)."""
    env = Env()
    purposes = env.service_type_purposes
    assert purposes["s2s"] == []
    assert purposes["s2s-android"] == []
    assert purposes["search"] == []


def test_service_type_requires_purpose():
    """Test that purpose is required only for service types with configured allowlists."""
    env = Env()
    assert env.service_type_requires_purpose("ai") is True
    assert env.service_type_requires_purpose("ai-dev") is True
    assert env.service_type_requires_purpose("mochi-dev") is True
    assert env.service_type_requires_purpose("memories") is True
    assert env.service_type_requires_purpose("memories-dev") is True
    assert env.service_type_requires_purpose("s2s") is False
    assert env.service_type_requires_purpose("s2s-android") is False
    assert env.service_type_requires_purpose("search") is False


def test_valid_purposes_for_service_type():
    """Test valid_purposes_for_service_type returns correct list per service type."""
    env = Env()
    assert set(env.valid_purposes_for_service_type("ai")) == {
        "chat",
        "title-generation",
        "convo-starters-sidebar",
        "telemetry",
    }
    assert env.valid_purposes_for_service_type("memories") == [
        "memory-generation",
        "telemetry",
    ]
    assert env.valid_purposes_for_service_type("s2s") == []
    assert env.valid_purposes_for_service_type("search") == []


def test_user_feature_budget_structure_consistency():
    """Test that all service types have the same structure in user_feature_budget."""
    env = Env()
    budgets = env.user_feature_budget

    # Get the keys from one service type as reference
    reference_keys = set(budgets["ai"].keys())

    # Verify all service types have the same keys
    for service_type in [
        "ai",
        "s2s",
        "s2s-android",
        "memories",
        "ai-dev",
        "memories-dev",
        "mochi-dev",
        "search",
    ]:
        assert service_type in budgets
        service_keys = set(budgets[service_type].keys())
        assert service_keys == reference_keys, (
            f"{service_type} has different keys than ai"
        )


def test_user_feature_budget_memories_type_validation():
    """Test that memories budget configuration values have correct types."""
    env = Env()
    memories_config = env.user_feature_budget["memories"]

    assert isinstance(memories_config["budget_id"], str)
    assert isinstance(memories_config["max_budget"], float)
    assert isinstance(memories_config["rpm_limit"], int)
    assert isinstance(memories_config["tpm_limit"], int)
    assert isinstance(memories_config["budget_duration"], str)


def test_forced_model_service_type_pairs_defaults():
    """Test that forced model/service-type mappings include search-only models."""
    env = Env()

    assert env.forced_model_service_type_pairs == {"exa": ["search"]}


def test_valid_service_type_for_model_forced_pair():
    """Test that forced model/service-type pairs are enforced."""
    env = Env()

    assert env.valid_service_type_for_model("search", "exa") is True
    assert env.valid_service_type_for_model("ai", "exa") is False


def test_valid_service_type_for_model_unconfigured_model():
    """Test that unconfigured models accept any service type."""
    env = Env()

    assert env.valid_service_type_for_model("ai", "gpt-oss-120b") is True
    assert env.valid_service_type_for_model("search", "gpt-oss-120b") is True
