from mlpa.core.config import env
from tests.consts import TEST_USER_ID


def test_block_user_success(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        TEST_USER_ID,
        {"user_id": TEST_USER_ID, "blocked": False, "alias": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/block",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
    )

    assert response.status_code == 200
    assert response.json()["blocked"] is True
    assert response.json()["user_id"] == TEST_USER_ID


def test_update_user_budget_success(mocked_client_integration, mocker):
    """Test updating a user's budget tier successfully."""
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        TEST_USER_ID,
        {"user_id": TEST_USER_ID, "blocked": False, "alias": None, "budget_id": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/budget",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
        json={"service_type": "ai-dev"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == TEST_USER_ID
    assert data["budget_id"] == "end-user-budget-ai-dev"
    assert data["service_type"] == "ai-dev"


def test_update_user_budget_unauthorized(mocked_client_integration):
    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/budget",
        headers={"master_key": "Bearer invalid-key"},
        json={"service_type": "ai-dev"},
    )

    assert response.status_code == 401
    assert "Unauthorized" in str(response.json())


def test_update_user_budget_user_not_found(mocked_client_integration, mocker):
    """Test updating budget for non-existent user returns 404."""
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/budget",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
        json={"service_type": "ai-dev"},
    )

    assert response.status_code == 404
    assert "User not found" in str(response.json())


def test_update_user_budget_invalid_service_type(mocked_client_integration, mocker):
    """Test that invalid service_type returns 422."""
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        TEST_USER_ID,
        {"user_id": TEST_USER_ID, "blocked": False, "alias": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/budget",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
        json={"service_type": "invalid-service"},
    )

    assert response.status_code == 422
    assert "Unknown service type" in str(response.json())


def test_block_user_unauthorized(mocked_client_integration):
    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/block",
        headers={"master_key": "Bearer invalid-key"},
    )

    assert response.status_code == 401
    assert "Unauthorized" in str(response.json())


def test_block_user_not_found(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/block",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
    )

    assert response.status_code == 404
    assert "User not found" in str(response.json())


def test_unblock_user_success(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        TEST_USER_ID,
        {"user_id": TEST_USER_ID, "blocked": True, "alias": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/unblock",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
    )

    assert response.status_code == 200
    assert response.json()["blocked"] is False
    assert response.json()["user_id"] == TEST_USER_ID


def test_unblock_user_unauthorized(mocked_client_integration):
    response = mocked_client_integration.post(
        f"/user/{TEST_USER_ID}/unblock",
        headers={"master_key": "Bearer invalid-key"},
    )

    assert response.status_code == 401
    assert "Unauthorized" in str(response.json())


def test_list_users_success(mocked_client_integration, mocker):
    """Test listing users successfully."""
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        "user1:ai",
        {"user_id": "user1:ai", "blocked": False, "alias": None},
    )
    mock_litellm_pg.store_user(
        "user2:ai",
        {"user_id": "user2:ai", "blocked": True, "alias": None},
    )
    mock_litellm_pg.store_user(
        "user3:s2s",
        {"user_id": "user3:s2s", "blocked": False, "alias": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.get(
        "/user",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
        params={"limit": 2, "offset": 0},
    )

    assert response.status_code == 200
    data = response.json()
    assert "users" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert data["total"] == 3
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert len(data["users"]) == 2


def test_list_users_pagination(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    for i in range(5):
        mock_litellm_pg.store_user(
            f"user{i}:ai",
            {"user_id": f"user{i}:ai", "blocked": False, "alias": None},
        )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.get(
        "/user",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
        params={"limit": 2, "offset": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert data["limit"] == 2
    assert data["offset"] == 2
    assert len(data["users"]) == 2


def test_list_users_unauthorized(mocked_client_integration):
    response = mocked_client_integration.get(
        "/user",
        headers={"master_key": "Bearer invalid-key"},
    )

    assert response.status_code == 401
    assert "Unauthorized" in str(response.json())


def test_list_users_default_params(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        "user1:ai",
        {"user_id": "user1:ai", "blocked": False, "alias": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.get(
        "/user",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 50
    assert data["offset"] == 0


def test_list_users_empty_result(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.get(
        "/user",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["users"]) == 0


def test_count_users_by_service_type_success(mocked_client_integration, mocker):
    from tests.mocks import MockLiteLLMPGService

    mock_litellm_pg = MockLiteLLMPGService()
    mock_litellm_pg.store_user(
        "user1:ai", {"user_id": "user1:ai", "blocked": False, "alias": None}
    )
    mock_litellm_pg.store_user(
        "user2:ai", {"user_id": "user2:ai", "blocked": False, "alias": None}
    )
    mock_litellm_pg.store_user(
        "user3:s2s",
        {"user_id": "user3:s2s", "blocked": False, "alias": None},
    )

    mocker.patch("mlpa.core.routers.user.user.litellm_pg", mock_litellm_pg)

    response = mocked_client_integration.get(
        "/user/counts-by-service-type",
        headers={"master_key": f"Bearer {env.MASTER_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_users"] == 3
    assert data["service_type_counts"]["ai"] == 2
    assert data["service_type_counts"]["s2s"] == 1


def test_count_users_by_service_type_unauthorized(mocked_client_integration):
    response = mocked_client_integration.get(
        "/user/counts-by-service-type",
        headers={"master_key": "Bearer invalid-key"},
    )

    assert response.status_code == 401
    assert "Unauthorized" in str(response.json())
