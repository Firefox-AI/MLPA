import os
from unittest.mock import patch

import pytest

from mlpa.core.config import Env


def test_user_feature_budget_includes_insights():
    """Test that user_feature_budget property includes insights service type."""
    env = Env()
    budgets = env.user_feature_budget

    # Verify insights is present
    assert "insights" in budgets
    assert isinstance(budgets["insights"], dict)

    # Verify insights has all required keys
    insights_config = budgets["insights"]
    assert "budget_id" in insights_config
    assert "max_budget" in insights_config
    assert "rpm_limit" in insights_config
    assert "tpm_limit" in insights_config
    assert "budget_duration" in insights_config


def test_user_feature_budget_insights_default_values():
    """Test that insights budget configuration has correct default values."""
    env = Env()
    insights_config = env.user_feature_budget["insights"]

    assert insights_config["budget_id"] == "end-user-budget-insights"
    assert insights_config["max_budget"] == 0.1
    assert insights_config["rpm_limit"] == 10
    assert insights_config["tpm_limit"] == 2000
    assert insights_config["budget_duration"] == "1d"


def test_user_feature_budget_insights_from_env():
    """Test that insights budget configuration can be overridden via environment variables."""
    env_vars = {
        "USER_FEATURE_BUDGET_INSIGHTS_BUDGET_ID": "custom-insights-budget-id",
        "USER_FEATURE_BUDGET_INSIGHTS_MAX_BUDGET": "0.5",
        "USER_FEATURE_BUDGET_INSIGHTS_RPM_LIMIT": "20",
        "USER_FEATURE_BUDGET_INSIGHTS_TPM_LIMIT": "5000",
        "USER_FEATURE_BUDGET_INSIGHTS_BUDGET_DURATION": "7d",
    }

    with patch.dict(os.environ, env_vars):
        env = Env()
        insights_config = env.user_feature_budget["insights"]

        assert insights_config["budget_id"] == "custom-insights-budget-id"
        assert insights_config["max_budget"] == 0.5
        assert insights_config["rpm_limit"] == 20
        assert insights_config["tpm_limit"] == 5000
        assert insights_config["budget_duration"] == "7d"


def test_valid_service_types_includes_insights():
    """Test that valid_service_types property includes insights."""
    env = Env()
    service_types = env.valid_service_types

    assert "insights" in service_types
    assert isinstance(service_types, list)


def test_valid_service_types_all_service_types():
    """Test that valid_service_types includes all three service types (ai, s2s, insights)."""
    env = Env()
    service_types = env.valid_service_types

    assert "ai" in service_types
    assert "s2s" in service_types
    assert "insights" in service_types
    assert len(service_types) == 3


def test_user_feature_budget_structure_consistency():
    """Test that all service types have the same structure in user_feature_budget."""
    env = Env()
    budgets = env.user_feature_budget

    # Get the keys from one service type as reference
    reference_keys = set(budgets["ai"].keys())

    # Verify all service types have the same keys
    for service_type in ["ai", "s2s", "insights"]:
        assert service_type in budgets
        service_keys = set(budgets[service_type].keys())
        assert service_keys == reference_keys, (
            f"{service_type} has different keys than ai"
        )


def test_user_feature_budget_insights_type_validation():
    """Test that insights budget configuration values have correct types."""
    env = Env()
    insights_config = env.user_feature_budget["insights"]

    assert isinstance(insights_config["budget_id"], str)
    assert isinstance(insights_config["max_budget"], float)
    assert isinstance(insights_config["rpm_limit"], int)
    assert isinstance(insights_config["tpm_limit"], int)
    assert isinstance(insights_config["budget_duration"], str)
