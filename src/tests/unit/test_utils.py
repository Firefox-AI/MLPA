import base64

import pytest
from fastapi import HTTPException

from mlpa.core.utils import (
    b64decode_safe,
    clamp_country,
    is_context_window_error,
    is_invalid_model_name_error,
    is_invalid_request_error,
    is_rate_limit_error,
)


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


def test_is_context_window_error_context_window_exceeded():
    """Test that ContextWindowExceededError is detected."""
    error_text = "litellm.ContextWindowExceededError: This model's maximum context length is 128000 tokens."
    assert is_context_window_error(error_text) is True


def test_is_context_window_error_maximum_context_length():
    """Test that 'maximum context length' message is detected."""
    error_text = '{"error": {"message": "maximum context length is 128000 tokens. Your messages resulted in 496095 tokens"}}'
    assert is_context_window_error(error_text) is True


def test_is_context_window_error_context_window_exceeded_literal():
    """Test that 'context window exceeded' string is detected."""
    error_text = "Error: context window exceeded for this model"
    assert is_context_window_error(error_text) is True


def test_is_context_window_error_context_length():
    """Test that 'context length' is detected."""
    error_text = "Invalid context length - too many tokens"
    assert is_context_window_error(error_text) is True


def test_is_context_window_error_no_match():
    """Test that non-context-window errors return False."""
    assert is_context_window_error("Invalid request parameters") is False
    assert is_context_window_error("Rate limit exceeded") is False
    assert is_context_window_error("") is False


def test_is_invalid_model_name_error_match():
    text = (
        '{"error": "/chat/completions: Invalid model name passed in model=foo. '
        'Call `/v1/models` to view available models for your key."}'
    )
    assert is_invalid_model_name_error(text) is True


def test_is_invalid_model_name_error_no_match():
    assert is_invalid_model_name_error("rate limit exceeded") is False
    assert is_invalid_model_name_error("") is False


def test_is_invalid_request_error_vertex_json():
    text = (
        "litellm.BadRequestError: Vertex_aiException BadRequestError - "
        '[{"error": {"code": 400, "message": "Expected a valid JSON object in the request", '
        '"status": "INVALID_ARGUMENT"}}]'
    )
    assert is_invalid_request_error(text) is True


def test_is_invalid_request_error_generic_bad_request_not_matched():
    text = "litellm.BadRequestError: SomeProviderException - something went wrong"
    assert is_invalid_request_error(text) is False


def test_is_invalid_request_error_vertex_pretty_printed():
    text = (
        "Vertex error:\n"
        "{\n"
        '  "error": {\n'
        '    "code": 400,\n'
        '    "status" : "INVALID_ARGUMENT"\n'
        "  }\n"
        "}"
    )
    assert is_invalid_request_error(text) is True


def test_is_invalid_request_error_anchored_not_substring():
    assert is_invalid_request_error('{"flag":"invalid_argument_count"}') is False


def test_is_invalid_request_error_no_match():
    assert is_invalid_request_error("Invalid request parameters") is False
    assert is_invalid_request_error("rate limit exceeded") is False
    assert is_invalid_request_error("") is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DE", "DE"),
        ("US", "US"),
        ("GB", "GB"),
        ("de", "unknown"),
        ("ZZ", "unknown"),
        ("**", "unknown"),
        ("--", "unknown"),
        ("USA", "unknown"),
        ("D", "unknown"),
        ("D1", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("DE; rm -rf", "unknown"),
    ],
)
def test_clamp_country(raw, expected):
    assert clamp_country(raw) == expected
