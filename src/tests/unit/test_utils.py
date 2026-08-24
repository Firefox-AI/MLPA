import base64

import pytest
from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.utils import (
    b64decode_safe,
    clamp_country,
    clamp_launch_country,
    clamp_purpose,
    clamp_service_type,
    is_context_window_error,
    is_invalid_model_name_error,
    is_invalid_request_error,
    is_plausible_base64_key_id,
    is_plausible_integrity_token,
    is_rate_limit_error,
    is_valid_model_name,
    parse_firefox_major_version_from_user_agent,
)

# Sourced from env.valid_model_labels (not hand-copied) so a future model name
# with an unexpected character is caught here instead of silently 400ing.
VALID_CLIENT_FACING_MODEL_NAMES = sorted(env.valid_model_labels)

SAMPLED_ATTACK_PAYLOADS = [
    "' OR (SELECT pg_sleep(6)) IS NULL --",
    "1'||sleep(27*1000)*ugxuhb||'",
    "'\"()&%<zzz><ScRiPt >MkKR(9785)</ScRiPt>",
    "str(__import__('time').sleep(9))+__import__('socket').gethostbyname(...)",
    "-1 OR 5*5=25 --",
    "1'||DBMS_PIPE.RECEIVE_MESSAGE(CHR(98)...",
    "|echo culqcs$()\\ wbjwmr\nz^xyu||a #",
]


@pytest.mark.parametrize("model", VALID_CLIENT_FACING_MODEL_NAMES)
def test_is_valid_model_name_accepts_real_model_names(model):
    assert is_valid_model_name(model) is True


@pytest.mark.parametrize("model", SAMPLED_ATTACK_PAYLOADS)
def test_is_valid_model_name_rejects_sampled_attack_payloads(model):
    assert is_valid_model_name(model) is False


@pytest.mark.parametrize(
    "model",
    [
        "",
        "-gpt-oss",
        "gpt-oss-",
        ".gpt-oss",
        "gpt-oss.",
        "a" * 65,
        "GPT-OSS-120B",
        "_gpt-oss",
        "gpt-oss_",
        "/gpt-oss",
        "gpt-oss/",
    ],
)
def test_is_valid_model_name_rejects_edge_cases(model):
    assert is_valid_model_name(model) is False


@pytest.mark.parametrize("model", ["openai/gpt-4o", "vertex_ai/mistral-small-2503"])
def test_is_valid_model_name_accepts_slash_and_underscore_namespaced_models(model):
    assert is_valid_model_name(model) is True


def test_is_valid_model_name_accepts_max_length():
    assert is_valid_model_name("a" * 64) is True


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:155.0) Gecko/20100101 Firefox/155.0",
            "155",
        ),
        ("Mozilla/5.0 Firefox/100.12", "100"),
        ("bruno-runtime/3.4.2", ""),
        ("", ""),
    ],
)
def test_parse_firefox_major_version_from_user_agent(user_agent, expected):
    assert parse_firefox_major_version_from_user_agent(user_agent) == expected


@pytest.mark.parametrize("key_id_b64", ["dGVzdC1rZXktaWQ=", "A" * 44, "a+b/c==", "x"])
def test_is_plausible_base64_key_id_accepts_valid(key_id_b64):
    assert is_plausible_base64_key_id(key_id_b64) is True


@pytest.mark.parametrize(
    "key_id_b64",
    [
        "",
        "a" * 129,
        *SAMPLED_ATTACK_PAYLOADS,
    ],
)
def test_is_plausible_base64_key_id_rejects_invalid(key_id_b64):
    assert is_plausible_base64_key_id(key_id_b64) is False


@pytest.mark.parametrize(
    "integrity_token", ["test-token", "a" * 10000, "header.payload.sig.iv.tag"]
)
def test_is_plausible_integrity_token_accepts_valid(integrity_token):
    assert is_plausible_integrity_token(integrity_token) is True


@pytest.mark.parametrize(
    "integrity_token",
    [
        "",
        "a" * 10001,
        *SAMPLED_ATTACK_PAYLOADS,
    ],
)
def test_is_plausible_integrity_token_rejects_invalid(integrity_token):
    assert is_plausible_integrity_token(integrity_token) is False


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
        ("", ""),
        ("ai", "ai"),
        ("memories", "memories"),
        ("not-a-service-type", "other"),
    ],
)
def test_clamp_service_type_preserves_missing_value(raw, expected):
    assert clamp_service_type(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("chat", "chat"),
        ("memory-generation", "memory-generation"),
        ("not-a-purpose", "other"),
    ],
)
def test_clamp_purpose_preserves_missing_value(raw, expected):
    assert clamp_purpose(raw) == expected


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("US", "US"),
        ("CA", "CA"),
        ("FR", "FR"),
        ("DE", "DE"),
        ("GB", "other"),
        ("us", "other"),
        ("", "other"),
        ("DE; rm -rf", "other"),
    ],
)
def test_clamp_launch_country(raw, expected):
    assert clamp_launch_country(raw) == expected
