import json
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from proxy.core.routers.mock.mock import verify_jwt_token_only
from tests.consts import TEST_FXA_TOKEN, TEST_USER_ID


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
		# The error message will be about JWT verification since the token parsing succeeds
		assert "Token verification failed" in exc_info.value.detail

	def test_empty_bearer_token(self):
		"""Test that empty Bearer token raises HTTPException."""
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only("Bearer ")

		assert exc_info.value.status_code == 401
		assert "Invalid authorization header format" in exc_info.value.detail

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_successful_jwt_verification(self, mock_fxa_client):
		"""Test successful JWT verification with valid token."""
		# Mock JWKS response
		mock_jwks_response = {
			"keys": [
				{
					"kty": "RSA",
					"kid": "test-key-id",
					"use": "sig",
					"n": "test-n",
					"e": "AQAB",
				}
			]
		}

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Mock successful JWT verification
		expected_result = {
			"user": TEST_USER_ID,
			"client_id": "test-client-id",
			"scope": ["profile"],
			"generation": 1,
			"profile_changed_at": 1234567890,
		}
		mock_fxa_client._verify_jwt_token.return_value = expected_result

		# Test
		result = verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		# Assertions
		assert result == expected_result
		mock_api_client.get.assert_called_once_with("/jwks")
		mock_fxa_client._verify_jwt_token.assert_called_once()

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_with_multiple_keys(self, mock_fxa_client):
		"""Test JWT verification when multiple keys are available."""
		# Mock JWKS response with multiple keys
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

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Mock JWT verification - first key fails, second succeeds
		expected_result = {"user": TEST_USER_ID}
		mock_fxa_client._verify_jwt_token.side_effect = [
			jwt.exceptions.InvalidSignatureError("Invalid signature"),
			expected_result,
		]

		# Test
		result = verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		# Assertions
		assert result == expected_result
		assert mock_fxa_client._verify_jwt_token.call_count == 2

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_invalid_signature_all_keys(self, mock_fxa_client):
		"""Test JWT verification when all keys fail signature validation."""
		# Mock JWKS response
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

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Mock JWT verification failure
		mock_fxa_client._verify_jwt_token.side_effect = (
			jwt.exceptions.InvalidSignatureError("Invalid signature")
		)

		# Test
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "Invalid token signature" in exc_info.value.detail

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_expired_token(self, mock_fxa_client):
		"""Test JWT verification with expired token."""
		# Mock JWKS response
		mock_jwks_response = {
			"keys": [
				{
					"kty": "RSA",
					"kid": "test-key-id",
					"use": "sig",
					"n": "test-n",
					"e": "AQAB",
				}
			]
		}

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Mock JWT verification failure due to expired token
		mock_fxa_client._verify_jwt_token.side_effect = (
			jwt.exceptions.ExpiredSignatureError("Token has expired")
		)

		# Test
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "JWT verification failed: Token has expired" in exc_info.value.detail

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_malformed_token(self, mock_fxa_client):
		"""Test JWT verification with malformed token."""
		# Mock JWKS response
		mock_jwks_response = {
			"keys": [
				{
					"kty": "RSA",
					"kid": "test-key-id",
					"use": "sig",
					"n": "test-n",
					"e": "AQAB",
				}
			]
		}

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Mock JWT verification failure due to malformed token
		mock_fxa_client._verify_jwt_token.side_effect = jwt.exceptions.DecodeError(
			"Invalid token format"
		)

		# Test
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "JWT verification failed: Invalid token format" in exc_info.value.detail

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_trust_error(self, mock_fxa_client):
		"""Test JWT verification with TrustError."""
		# Mock JWKS response
		mock_jwks_response = {
			"keys": [
				{
					"kty": "RSA",
					"kid": "test-key-id",
					"use": "sig",
					"n": "test-n",
					"e": "AQAB",
				}
			]
		}

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Mock TrustError
		from fxa.errors import TrustError

		mock_fxa_client._verify_jwt_token.side_effect = TrustError(
			"Token trust validation failed"
		)

		# Test
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert (
			"Token trust error: Token trust validation failed" in exc_info.value.detail
		)

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_api_error(self, mock_fxa_client):
		"""Test JWT verification when API call fails."""
		# Mock API client get method to raise an exception
		mock_api_client = MagicMock()
		mock_api_client.get.side_effect = Exception("Network error")
		mock_fxa_client.apiclient = mock_api_client

		# Test
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "Token verification failed: Network error" in exc_info.value.detail

	@patch("proxy.core.routers.mock.mock.fxa_client")
	def test_jwt_verification_empty_jwks_response(self, mock_fxa_client):
		"""Test JWT verification when JWKS response has no keys."""
		# Mock empty JWKS response
		mock_jwks_response = {"keys": []}

		# Mock the API client get method
		mock_api_client = MagicMock()
		mock_api_client.get.return_value = mock_jwks_response
		mock_fxa_client.apiclient = mock_api_client

		# Test
		with pytest.raises(HTTPException) as exc_info:
			verify_jwt_token_only(f"Bearer {TEST_FXA_TOKEN}")

		assert exc_info.value.status_code == 401
		assert "Invalid token signature" in exc_info.value.detail
