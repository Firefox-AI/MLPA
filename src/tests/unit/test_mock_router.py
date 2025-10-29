from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from fxa.errors import TrustError

from mlpa.core.routers.mock.mock import verify_jwt_token_only
from tests.consts import (
	MOCK_FXA_USER_DATA,
	MOCK_JWKS_RESPONSE,
	TEST_FXA_TOKEN,
	TEST_USER_ID,
)


class TestVerifyJwtTokenOnly:
	"""Unit tests for the verify_jwt_token_only function."""

	def test_missing_authorization_header(self):
		"""Test that missing authorization header raises HTTPException."""
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(None)

		assert exc_info.value.status_code == 401
		assert "Missing FxA authorization header" in exc_info.value.detail

	def test_invalid_authorization_header_format(self):
		"""Test that invalid authorization header format raises HTTPException."""
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only("InvalidFormat")

		assert exc_info.value.status_code == 401
		assert "Token verification failed" in exc_info.value.detail

	def test_empty_bearer_token(self):
		"""Test that empty Bearer token raises HTTPException."""
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only("Bearer ")

		assert exc_info.value.status_code == 401
		assert "Invalid authorization header format" in exc_info.value.detail

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_successful_jwt_verification(self, mock_fxa_client):
		"""Test successful JWT verification with valid token."""

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
		mock_fxa_client.apiclient = mock_api_client

		expected_result = MOCK_FXA_USER_DATA
		mock_fxa_client._verify_jwt_token.return_value = expected_result

		result = verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert result == expected_result
		mock_api_client.get.assert_called_once_with("/jwks")
		mock_fxa_client._verify_jwt_token.assert_called_once()

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_with_multiple_keys(self, mock_fxa_client):
		"""Test JWT verification when multiple keys are available."""
		mock_jwks_response = {
			"keys": [
				{
					"kty": "RSA",
					"kid": "key-1",
					"use": "sig",
					"n": "test-n-1",
					"e": "AQAB",
				},
				{
					"kty": "RSA",
					"kid": "key-2",
					"use": "sig",
					"n": "test-n-2",
					"e": "AQAB",
				},
			]
		}

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		expected_result = {"user": TEST_USER_ID}
		mock_fxa_client._verify_jwt_token.side_effect = [
			jwt.exceptions.InvalidSignatureError("Invalid signature"),
			expected_result,
		]

		result = verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert result == expected_result
		assert mock_fxa_client._verify_jwt_token.call_count == 2

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_invalid_signature_all_keys(self, mock_fxa_client):
		"""Test JWT verification when all keys fail signature validation."""
		mock_jwks_response = {
			"keys": [
				{
					"kty": "RSA",
					"kid": "key-1",
					"use": "sig",
					"n": "test-n-1",
					"e": "AQAB",
				}
			]
		}

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		mock_fxa_client._verify_jwt_token.side_effect = (
			jwt.exceptions.InvalidSignatureError("Invalid signature")
		)

		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "Invalid token signature" in exc_info.value.detail

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_expired_token(self, mock_fxa_client):
		"""Test JWT verification with expired token."""

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
		mock_fxa_client.apiclient = mock_api_client

		mock_fxa_client._verify_jwt_token.side_effect = (
			jwt.exceptions.ExpiredSignatureError("Token has expired")
		)

		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "JWT verification failed: Token has expired" in exc_info.value.detail

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_malformed_token(self, mock_fxa_client):
		"""Test JWT verification with malformed token."""

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
		mock_fxa_client.apiclient = mock_api_client

		# Mock JWT verification failure due to malformed token
		mock_fxa_client._verify_jwt_token.side_effect = jwt.exceptions.DecodeError(
			"Invalid token format"
		)

		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "JWT verification failed: Invalid token format" in exc_info.value.detail

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_trust_error(self, mock_fxa_client):
		"""Test JWT verification with TrustError."""

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
		mock_fxa_client.apiclient = mock_api_client

		mock_fxa_client._verify_jwt_token.side_effect = TrustError(
			"Token trust validation failed"
		)

		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert (
			"Token trust error: Token trust validation failed" in exc_info.value.detail
		)

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_api_error(self, mock_fxa_client):
		"""Test JWT verification when API call fails."""
		mock_api_client = MagicMock()
		mock_api_client.get.side_effect = Exception("Network error")
		mock_fxa_client.apiclient = mock_api_client

		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "Token verification failed: Network error" in exc_info.value.detail

	@patch("mlpa.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_empty_jwks_response(self, mock_fxa_client):
		"""Test JWT verification when JWKS response has no keys."""
		mock_jwks_response = {"keys": []}

		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "Invalid token signature" in exc_info.value.detail
