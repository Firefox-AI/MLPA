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


def test_valid_service_types_all_service_types():
    """Test that valid_service_types includes all three service types (ai, s2s, memories)."""
    env = Env()
    service_types = env.valid_service_types

    assert "ai" in service_types
    assert "s2s" in service_types
    assert "memories" in service_types
    assert len(service_types) == 3


def test_user_feature_budget_structure_consistency():
    """Test that all service types have the same structure in user_feature_budget."""
    env = Env()
    budgets = env.user_feature_budget

    # Get the keys from one service type as reference
    reference_keys = set(budgets["ai"].keys())

    # Verify all service types have the same keys
    for service_type in ["ai", "s2s", "memories"]:
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
