from mlpa.core.config import env


def test_security_headers_present(mocked_client_integration):
    """Test that security headers are present in API responses."""
    response = mocked_client_integration.get("/health/liveness")

    assert response.status_code == 200
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_hsts_header_present_for_https(mocked_client_integration):
    """Test that HSTS header is present when X-Forwarded-Proto is https."""
    response = mocked_client_integration.get(
        "/health/liveness", headers={"X-Forwarded-Proto": "https"}
    )

    assert response.status_code == 200
    assert "Strict-Transport-Security" in response.headers
    hsts_value = response.headers["Strict-Transport-Security"]
    assert "max-age=" in hsts_value
    assert str(env.HSTS_MAX_AGE) in hsts_value


def test_hsts_header_not_present_for_http(mocked_client_integration):
    """Test that HSTS header is not present when X-Forwarded-Proto is http."""
    response = mocked_client_integration.get(
        "/health/liveness", headers={"X-Forwarded-Proto": "http"}
    )

    assert response.status_code == 200
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_header_includes_subdomains(mocked_client_integration):
    """Test that HSTS header includes includeSubDomains when configured."""
    response = mocked_client_integration.get(
        "/health/liveness", headers={"X-Forwarded-Proto": "https"}
    )

    assert response.status_code == 200
    if env.HSTS_INCLUDE_SUBDOMAINS:
        assert "includeSubDomains" in response.headers["Strict-Transport-Security"]


def test_security_headers_on_all_endpoints(mocked_client_integration, httpx_mock):
    """Test that security headers are present on all API endpoints."""
    httpx_mock.add_response(
        method="GET",
        url=f"{env.LITELLM_API_BASE}/health/readiness",
        status_code=200,
        json={"status": "connected"},
    )

    endpoints = [
        "/health/liveness",
        "/health/readiness",
        "/metrics",
    ]

    for endpoint in endpoints:
        response = mocked_client_integration.get(endpoint)
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            assert "X-Content-Type-Options" in response.headers
            assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_security_headers_configurable(mocked_client_integration, mocker):
    """Test that security headers can be disabled via configuration."""
    mocker.patch(
        "mlpa.core.middleware.security_headers.env.SECURITY_HEADERS_ENABLED", False
    )

    import importlib

    from mlpa.core import middleware

    importlib.reload(middleware.security_headers)

    response = mocked_client_integration.get("/health/liveness")

    assert response.status_code == 200
    assert "X-Content-Type-Options" not in response.headers

    importlib.reload(middleware.security_headers)
