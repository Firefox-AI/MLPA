from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.consts import SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN, TEST_USER_ID


class TestMockRouterIntegration:
	"""Integration tests for the mock router endpoints."""

	def test_mock_chat_completions_success(self, mocked_client_integration):
		"""Test successful request to /mock/chat/completions endpoint."""
		response = mocked_client_integration.post(
			"/mock/chat/completions",
			headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
			json={
				"model": "mock-gpt",
				"messages": [{"role": "user", "content": "Hello"}],
				"temperature": 0.7,
				"top_p": 0.9,
				"max_completion_tokens": 150,
			},
		)

		assert response.status_code == 200
		response_data = response.json()
		assert (
			response_data["choices"][0]["message"]["content"]
			== "mock completion response"
		)
		assert response_data["model"] == "mock-gpt"
		assert response_data["usage"]["prompt_tokens"] == 10
		assert response_data["usage"]["completion_tokens"] == 5
		assert response_data["usage"]["total_tokens"] == 15

	def test_mock_chat_completions_streaming(self, mocked_client_integration):
		"""Test streaming request to /mock/chat/completions endpoint."""
		response = mocked_client_integration.post(
			"/mock/chat/completions",
			headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
			json={
				"model": "mock-gpt",
				"messages": [{"role": "user", "content": "Hello"}],
				"stream": True,
			},
		)

		assert response.status_code == 200
		assert response.headers["content-type"].startswith("text/event-stream")

		# Check streaming content
		content = response.text
		assert 'data: {"choices":[{"delta":{"content":"mock token 1"}}]}\n\n' in content
		assert 'data: {"choices":[{"delta":{"content":"mock token 2"}}]}\n\n' in content
		assert "data: [DONE]\n\n" in content

	def test_mock_chat_completions_missing_auth(self, mocked_client_integration):
		"""Test /mock/chat/completions endpoint with missing authentication."""
		response = mocked_client_integration.post(
			"/mock/chat/completions",
			json={
				"model": "mock-gpt",
				"messages": [{"role": "user", "content": "Hello"}],
			},
		)

		assert response.status_code == 401
		assert "Please authenticate with App Attest or FxA" in response.json()["detail"]

	def test_mock_chat_completions_invalid_auth(self, mocked_client_integration):
		"""Test /mock/chat/completions endpoint with invalid authentication."""
		response = mocked_client_integration.post(
			"/mock/chat/completions",
			headers={"x-fxa-authorization": "Bearer invalid-token"},
			json={
				"model": "mock-gpt",
				"messages": [{"role": "user", "content": "Hello"}],
			},
		)

		assert response.status_code == 401

	def test_mock_chat_completions_no_auth_success(self, mocked_client_integration):
		"""Test successful request to /mock/chat/completions_no_auth endpoint."""
		# Mock the JWT verification to return valid user data
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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
			mock_fxa_client._verify_jwt_token.return_value = {
				"user": TEST_USER_ID,
				"client_id": "test-client-id",
				"scope": ["profile"],
			}

			response = mocked_client_integration.post(
				"/mock/chat/completions_no_auth",
				headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
				json={
					"model": "mock-gpt",
					"messages": [{"role": "user", "content": "Hello"}],
					"temperature": 0.7,
					"top_p": 0.9,
					"max_completion_tokens": 150,
				},
			)

			assert response.status_code == 200
			response_data = response.json()
			assert (
				response_data["choices"][0]["message"]["content"]
				== "mock completion response"
			)
			assert response_data["model"] == "mock-gpt"
			assert response_data["usage"]["prompt_tokens"] == 10
			assert response_data["usage"]["completion_tokens"] == 5
			assert response_data["usage"]["total_tokens"] == 15

	def test_mock_chat_completions_no_auth_streaming(self, mocked_client_integration):
		"""Test streaming request to /mock/chat/completions_no_auth endpoint."""
		# Mock the JWT verification to return valid user data
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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
			mock_fxa_client._verify_jwt_token.return_value = {
				"user": TEST_USER_ID,
				"client_id": "test-client-id",
				"scope": ["profile"],
			}

			response = mocked_client_integration.post(
				"/mock/chat/completions_no_auth",
				headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
				json={
					"model": "mock-gpt",
					"messages": [{"role": "user", "content": "Hello"}],
					"stream": True,
				},
			)

		assert response.status_code == 200
		assert response.headers["content-type"].startswith("text/event-stream")

		# Check streaming content
		content = response.text
		assert 'data: {"choices":[{"delta":{"content":"mock token 1"}}]}\n\n' in content
		assert 'data: {"choices":[{"delta":{"content":"mock token 2"}}]}\n\n' in content
		assert "data: [DONE]\n\n" in content

	def test_mock_chat_completions_no_auth_missing_header(
		self, mocked_client_integration
	):
		"""Test /mock/chat/completions_no_auth endpoint with missing authorization header."""
		response = mocked_client_integration.post(
			"/mock/chat/completions_no_auth",
			json={
				"model": "mock-gpt",
				"messages": [{"role": "user", "content": "Hello"}],
			},
		)

		assert (
			response.status_code == 422
		)  # FastAPI validation error for missing required header

	def test_mock_chat_completions_no_auth_invalid_token(
		self, mocked_client_integration
	):
		"""Test /mock/chat/completions_no_auth endpoint with invalid JWT token."""
		# Mock the JWT verification to fail
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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

			# Mock JWT verification failure
			import jwt

			mock_fxa_client._verify_jwt_token.side_effect = (
				jwt.exceptions.ExpiredSignatureError("Token has expired")
			)

			response = mocked_client_integration.post(
				"/mock/chat/completions_no_auth",
				headers={"x-fxa-authorization": "Bearer invalid-token"},
				json={
					"model": "mock-gpt",
					"messages": [{"role": "user", "content": "Hello"}],
				},
			)

			assert response.status_code == 401
			assert (
				"JWT verification failed: Token has expired"
				in response.json()["detail"]
			)

	def test_mock_chat_completions_no_auth_invalid_signature(
		self, mocked_client_integration
	):
		"""Test /mock/chat/completions_no_auth endpoint with invalid token signature."""
		# Mock the JWT verification to fail with invalid signature
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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

			# Mock JWT verification failure with invalid signature
			import jwt

			mock_fxa_client._verify_jwt_token.side_effect = (
				jwt.exceptions.InvalidSignatureError("Invalid signature")
			)

			response = mocked_client_integration.post(
				"/mock/chat/completions_no_auth",
				headers={"x-fxa-authorization": "Bearer invalid-signature-token"},
				json={
					"model": "mock-gpt",
					"messages": [{"role": "user", "content": "Hello"}],
				},
			)

			assert response.status_code == 401
			assert "Invalid token signature" in response.json()["detail"]

	def test_mock_chat_completions_no_auth_missing_user_in_token(
		self, mocked_client_integration
	):
		"""Test /mock/chat/completions_no_auth endpoint when JWT token doesn't contain user."""
		# Mock the JWT verification to return token without user
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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

			# Mock JWT verification returning token without user
			mock_fxa_client._verify_jwt_token.return_value = {
				"client_id": "test-client-id",
				"scope": ["profile"],
				# Missing "user" field
			}

			response = mocked_client_integration.post(
				"/mock/chat/completions_no_auth",
				headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
				json={
					"model": "mock-gpt",
					"messages": [{"role": "user", "content": "Hello"}],
				},
			)

		assert response.status_code == 400
		assert "User not found from JWT token" in response.json()["detail"]["error"]

	def test_mock_chat_completions_no_auth_blocked_user(
		self, mocked_client_integration
	):
		"""Test /mock/chat/completions_no_auth endpoint with blocked user."""
		# Mock the JWT verification to return valid user data
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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
			mock_fxa_client._verify_jwt_token.return_value = {
				"user": "blocked-user-id",
				"client_id": "test-client-id",
				"scope": ["profile"],
			}

			# Mock get_or_create_user to return a blocked user
			with patch(
				"proxy.core.routers.mock.mock.get_or_create_user",
				new_callable=AsyncMock,
			) as mock_get_user:
				mock_get_user.return_value = (
					{"user_id": "blocked-user-id", "blocked": True},
					False,
				)

				response = mocked_client_integration.post(
					"/mock/chat/completions_no_auth",
					headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
					json={
						"model": "mock-gpt",
						"messages": [{"role": "user", "content": "Hello"}],
					},
				)

			assert response.status_code == 403
			assert "User is blocked" in response.json()["detail"]["error"]

	def test_mock_chat_completions_latency_simulation(self, mocked_client_integration):
		"""Test that mock endpoints simulate latency correctly."""
		import time

		# Mock the JWT verification to return valid user data
		with patch("proxy.core.routers.mock.mock.fxa_client") as mock_fxa_client:
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
			mock_fxa_client._verify_jwt_token.return_value = {
				"user": TEST_USER_ID,
				"client_id": "test-client-id",
				"scope": ["profile"],
			}

			# Set a custom latency for testing
			with patch.dict("os.environ", {"MOCK_LATENCY_MS": "100"}):
				start_time = time.time()
				response = mocked_client_integration.post(
					"/mock/chat/completions_no_auth",
					headers={"x-fxa-authorization": f"Bearer {TEST_FXA_TOKEN}"},
					json={
						"model": "mock-gpt",
						"messages": [{"role": "user", "content": "Hello"}],
					},
				)
				end_time = time.time()

				assert response.status_code == 200
				# Should take at least 100ms (0.1 seconds)
				assert (end_time - start_time) >= 0.1
