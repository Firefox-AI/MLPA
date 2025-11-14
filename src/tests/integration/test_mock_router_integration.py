from unittest.mock import AsyncMock, MagicMock, patch

from tests.consts import (
    MOCK_FXA_USER_DATA,
    MOCK_JWKS_RESPONSE,
    MOCK_MODEL_NAME,
    SAMPLE_CHAT_REQUEST,
    TEST_FXA_TOKEN,
    TEST_USER_ID,
)

sample_chat_request = SAMPLE_CHAT_REQUEST.model_dump(exclude_unset=True)


class TestMockRouterIntegration:
    """Integration tests for the mock router endpoints."""

    def test_mock_chat_completions_success(self, mocked_client_integration):
        """Test successful request to /mock/chat/completions endpoint."""
        response = mocked_client_integration.post(
            "/mock/chat/completions",
            headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
            json=sample_chat_request,
        )

        assert response.status_code == 200
        response_data = response.json()
        assert (
            response_data["choices"][0]["message"]["content"]
            == "mock completion response"
        )
        assert response_data["model"] == MOCK_MODEL_NAME
        assert response_data["usage"]["prompt_tokens"] == 10
        assert response_data["usage"]["completion_tokens"] == 5
        assert response_data["usage"]["total_tokens"] == 15

    def test_mock_chat_completions_streaming(self, mocked_client_integration):
        """Test streaming request to /mock/chat/completions endpoint."""
        response = mocked_client_integration.post(
            "/mock/chat/completions",
            headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
            json={
                **sample_chat_request,
                "stream": True,
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        content = response.text
        assert 'data: {"choices":[{"delta":{"content":"mock token 1"}}]}\n\n' in content
        assert 'data: {"choices":[{"delta":{"content":"mock token 2"}}]}\n\n' in content
        assert "data: [DONE]\n\n" in content

    def test_mock_chat_completions_missing_auth(self, mocked_client_integration):
        """Test /mock/chat/completions endpoint with missing authentication."""
        response = mocked_client_integration.post(
            "/mock/chat/completions",
            json=sample_chat_request,
        )

        assert response.status_code == 401
        assert "Missing authorization header" in response.json()["detail"]

    def test_mock_chat_completions_invalid_auth(self, mocked_client_integration):
        """Test /mock/chat/completions endpoint with invalid authentication."""
        response = mocked_client_integration.post(
            "/mock/chat/completions",
            headers={"authorization": "Bearer invalid-token"},
            json={
                "model": MOCK_MODEL_NAME,
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

        assert response.status_code == 401

    def test_mock_chat_completions_no_auth_success(self, mocked_client_integration):
        """Test successful request to /mock/chat/completions_no_auth endpoint."""
        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            mock_fxa_client._verify_jwt_token.return_value = MOCK_FXA_USER_DATA

            response = mocked_client_integration.post(
                "/mock/chat/completions_no_auth",
                headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
                json=sample_chat_request,
            )

            assert response.status_code == 200
            response_data = response.json()
            assert (
                response_data["choices"][0]["message"]["content"]
                == "mock completion response"
            )
            assert response_data["model"] == MOCK_MODEL_NAME
            assert response_data["usage"]["prompt_tokens"] == 10
            assert response_data["usage"]["completion_tokens"] == 5
            assert response_data["usage"]["total_tokens"] == 15

    def test_mock_chat_completions_no_auth_streaming(self, mocked_client_integration):
        """Test streaming request to /mock/chat/completions_no_auth endpoint."""
        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            mock_fxa_client._verify_jwt_token.return_value = MOCK_FXA_USER_DATA

            response = mocked_client_integration.post(
                "/mock/chat/completions_no_auth",
                headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
                json={
                    "model": MOCK_MODEL_NAME,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

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
            json=sample_chat_request,
        )

        assert response.status_code == 422

    def test_mock_chat_completions_no_auth_invalid_token(
        self, mocked_client_integration
    ):
        """Test /mock/chat/completions_no_auth endpoint with invalid JWT token."""
        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            import jwt

            mock_fxa_client._verify_jwt_token.side_effect = (
                jwt.exceptions.ExpiredSignatureError("Token has expired")
            )

            response = mocked_client_integration.post(
                "/mock/chat/completions_no_auth",
                headers={"authorization": "Bearer invalid-token"},
                json={
                    "model": MOCK_MODEL_NAME,
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
        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            import jwt

            mock_fxa_client._verify_jwt_token.side_effect = (
                jwt.exceptions.InvalidSignatureError("Invalid signature")
            )

            response = mocked_client_integration.post(
                "/mock/chat/completions_no_auth",
                headers={"authorization": "Bearer invalid-signature-token"},
                json={
                    "model": MOCK_MODEL_NAME,
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )

            assert response.status_code == 401
            assert "Invalid token signature" in response.json()["detail"]

    def test_mock_chat_completions_no_auth_missing_user_in_token(
        self, mocked_client_integration
    ):
        """Test /mock/chat/completions_no_auth endpoint when JWT token doesn't contain user."""
        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            mock_fxa_client._verify_jwt_token.return_value = {
                "client_id": "test-client-id",
                "scope": ["profile"],
            }

            response = mocked_client_integration.post(
                "/mock/chat/completions_no_auth",
                headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
                json={
                    "model": MOCK_MODEL_NAME,
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )

        assert response.status_code == 400
        assert "User not found from JWT token" in response.json()["detail"]["error"]

    def test_mock_chat_completions_no_auth_blocked_user(
        self, mocked_client_integration
    ):
        """Test /mock/chat/completions_no_auth endpoint with blocked user."""
        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            mock_fxa_client._verify_jwt_token.return_value = {
                "user": "blocked-user-id",
                "client_id": "test-client-id",
                "scope": ["profile"],
            }

            with patch(
                "mlpa.core.routers.mock.mock.get_or_create_user",
                new_callable=AsyncMock,
            ) as mock_get_user:
                mock_get_user.return_value = (
                    {"user_id": "blocked-user-id", "blocked": True},
                    False,
                )

                response = mocked_client_integration.post(
                    "/mock/chat/completions_no_auth",
                    headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
                    json={
                        "model": MOCK_MODEL_NAME,
                        "messages": [{"role": "user", "content": "Hello"}],
                    },
                )

            assert response.status_code == 403
            assert "User is blocked" in response.json()["detail"]["error"]

    def test_mock_chat_completions_latency_simulation(self, mocked_client_integration):
        """Test that mock endpoints simulate latency correctly."""
        import time

        with patch("mlpa.core.routers.mock.mock.fxa_client") as mock_fxa_client:
            mock_api_client = MagicMock()
            mock_api_client.get.return_value = MOCK_JWKS_RESPONSE
            mock_fxa_client.apiclient = mock_api_client

            mock_fxa_client._verify_jwt_token.return_value = {
                "user": TEST_USER_ID,
                "client_id": "test-client-id",
                "scope": ["profile"],
            }

            with patch.dict("os.environ", {"MOCK_LATENCY_MS": "100"}):
                start_time = time.time()
                response = mocked_client_integration.post(
                    "/mock/chat/completions_no_auth",
                    headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
                    json={
                        "model": MOCK_MODEL_NAME,
                        "messages": [{"role": "user", "content": "Hello"}],
                    },
                )
                end_time = time.time()

                assert response.status_code == 200
                assert (end_time - start_time) >= 0.1
