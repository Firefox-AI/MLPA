from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from mlpa.core.utils import get_or_create_user

_USER_ID = "user123:ai"
_BASE_IDENTITY, _, _ = _USER_ID.partition(":")
_BUDGET_ID = "end-user-budget-ai"
_DB_USER = {"user_id": _USER_ID, "blocked": False, "budget_id": _BUDGET_ID}


@pytest.fixture
def mock_litellm_pg(mocker):
    mock = AsyncMock()
    mocker.patch("mlpa.core.utils.litellm_pg", mock)
    return mock


@pytest.fixture
def mock_app_attest_pg(mocker):
    mock = AsyncMock()
    mocker.patch("mlpa.core.utils.app_attest_pg", mock)
    return mock


@pytest.fixture
def mock_http_client(mocker):
    client = AsyncMock()
    response = MagicMock()
    response.json.return_value = _DB_USER
    client.get.return_value = response
    client.post.return_value = MagicMock()
    mocker.patch("mlpa.core.utils.get_http_client", return_value=client)
    return client


async def test_existing_user_returns_db_user_without_http_call(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = _DB_USER

    user, was_created = await get_or_create_user(_USER_ID)

    assert user == _DB_USER
    assert was_created is False
    mock_http_client.get.assert_not_called()
    mock_http_client.post.assert_not_called()


async def test_new_user_created_and_returned(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None

    user, was_created = await get_or_create_user(_USER_ID)

    assert user == _DB_USER
    assert was_created is True
    mock_http_client.post.assert_awaited_once()
    mock_http_client.get.assert_awaited_once()


async def test_new_user_posts_correct_budget_id(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None

    await get_or_create_user(_USER_ID)

    _, kwargs = mock_http_client.post.call_args
    assert kwargs["json"]["user_id"] == _USER_ID
    assert kwargs["json"]["budget_id"] == _BUDGET_ID


async def test_new_user_with_cap_enforcement_admitted(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)

    with patch("mlpa.core.utils.env.MLPA_ENFORCE_SIGNIN_CAP", True):
        user, was_created = await get_or_create_user(_USER_ID)

    assert was_created is True
    mock_app_attest_pg.admit_managed_base_identity.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY
    )


async def test_new_user_with_cap_enforcement_rejected(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None
    mock_app_attest_pg.admit_managed_base_identity.return_value = (False, False)

    with patch("mlpa.core.utils.env.MLPA_ENFORCE_SIGNIN_CAP", True):
        with pytest.raises(HTTPException) as exc:
            await get_or_create_user(_USER_ID)

    assert exc.value.status_code == 403
    mock_http_client.post.assert_not_called()


async def test_new_user_creation_fails_releases_claimed_slot(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)
    mock_http_client.get.return_value.json.return_value = {}

    with patch("mlpa.core.utils.env.MLPA_ENFORCE_SIGNIN_CAP", True):
        with pytest.raises(HTTPException) as exc:
            await get_or_create_user(_USER_ID)

    assert exc.value.status_code == 500
    mock_app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY
    )


async def test_new_user_creation_fails_no_slot_to_release(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None
    mock_http_client.get.return_value.json.return_value = {}

    with pytest.raises(HTTPException) as exc:
        await get_or_create_user(_USER_ID)

    assert exc.value.status_code == 500
    mock_app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users.assert_not_called()


async def test_invalid_user_id_format_raises_400(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    with pytest.raises(HTTPException) as exc:
        await get_or_create_user("user-without-service-type")

    assert exc.value.status_code == 400


async def test_db_error_raises_500(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.side_effect = RuntimeError("DB connection failed")

    with pytest.raises(HTTPException) as exc:
        await get_or_create_user(_USER_ID)

    assert exc.value.status_code == 500


async def test_unexpected_exception_with_claimed_identity_releases_slot(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)
    mock_http_client.post.side_effect = RuntimeError("Network error")

    with patch("mlpa.core.utils.env.MLPA_ENFORCE_SIGNIN_CAP", True):
        with pytest.raises(HTTPException) as exc:
            await get_or_create_user(_USER_ID)

    assert exc.value.status_code == 500
    mock_app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY
    )
