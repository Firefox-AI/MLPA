import base64

import pytest
from fastapi import HTTPException

from mlpa.core.utils import b64decode_safe, is_rate_limit_error


def test_b64decode_safe():
    # Valid base64 string
    original_data = b"Test data for base64"
    encoded_data = base64.b64encode(original_data).decode("utf-8")
    decoded_data = b64decode_safe(encoded_data)
    assert decoded_data == original_data

    # Invalid base64 string
    invalid_encoded_data = "Invalid@@Base64!!"
    data_name = "custom_name"
    with pytest.raises(HTTPException) as exc_info:
        b64decode_safe(invalid_encoded_data, data_name)

    assert exc_info.value.status_code == 400
    assert "Invalid Base64" in exc_info.value.detail[data_name]


def test_is_rate_limit_error_budget_exceeded():
    """Test that budget exceeded errors are detected correctly."""
    error_response = {
        "error": {
            "message": "Budget has been exceeded! Current cost: 0.001565, Max budget: 0.001",
            "type": "budget_exceeded",
            "code": "400",
        }
    }
    assert is_rate_limit_error(error_response, ["budget"]) is True
    assert is_rate_limit_error(error_response, ["rate"]) is False


def test_is_rate_limit_error_rate_limit_exceeded():
    """Test that rate limit exceeded errors are detected correctly."""
    error_response = {
        "error": {
            "message": "Rate limit exceeded. TPM: 1000/500",
            "type": "rate_limit_exceeded",
            "code": "429",
        }
    }
    assert is_rate_limit_error(error_response, ["rate"]) is True
    assert is_rate_limit_error(error_response, ["budget"]) is False


def test_is_rate_limit_error_budget_in_message():
    """Test that 'budget' keyword in message is detected."""
    error_response = {
        "error": {
            "message": "Your budget limit has been reached",
            "type": "error",
            "code": "400",
        }
    }
    assert is_rate_limit_error(error_response, ["budget"]) is True


def test_is_rate_limit_error_rate_in_message():
    """Test that 'rate' keyword in message is detected."""
    error_response = {
        "error": {
            "message": "Rate limit exceeded for this user",
            "type": "error",
            "code": "429",
        }
    }
    assert is_rate_limit_error(error_response, ["rate"]) is True


def test_is_rate_limit_error_case_insensitive():
    """Test that keyword matching is case-insensitive."""
    error_response = {
        "error": {"message": "BUDGET exceeded", "type": "ERROR", "code": "400"}
    }
    assert is_rate_limit_error(error_response, ["budget"]) is True


def test_is_rate_limit_error_no_match():
    """Test that non-rate-limit errors return False."""
    error_response = {
        "error": {
            "message": "Invalid request parameters",
            "type": "invalid_request",
            "code": "400",
        }
    }
    assert is_rate_limit_error(error_response, ["budget"]) is False
    assert is_rate_limit_error(error_response, ["rate"]) is False


def test_is_rate_limit_error_missing_error_key():
    """Test that missing error key returns False."""
    error_response = {}
    assert is_rate_limit_error(error_response, ["budget"]) is False


def test_is_rate_limit_error_empty_error():
    """Test that empty error dict returns False."""
    error_response = {"error": {}}
    assert is_rate_limit_error(error_response, ["budget"]) is False


def test_is_rate_limit_error_multiple_keywords():
    """Test that any matching keyword returns True."""
    error_response = {
        "error": {"message": "Budget limit exceeded", "type": "error", "code": "400"}
    }
    assert is_rate_limit_error(error_response, ["budget", "rate"]) is True
    assert is_rate_limit_error(error_response, ["rate", "budget"]) is True
