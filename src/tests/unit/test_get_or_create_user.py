from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from mlpa.core.config import (
    ERROR_CODE_MAX_GUESTS_REACHED,
    GUEST_CAPACITY_ID,
    USER_CAPACITY_ID,
    Env,
    env,
)
from mlpa.core.utils import get_or_create_user

_USER_ID = "user123:ai"
_BASE_IDENTITY, _, _ = _USER_ID.partition(":")
_BUDGET_ID = env.service_type_config["ai"]["budget_id"]
_GUEST_USER_ID = f"{_BASE_IDENTITY}:s2s"
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
    mock_litellm_pg.get_user.side_effect = [None, _DB_USER]

    user, was_created = await get_or_create_user(_USER_ID)

    assert user == _DB_USER
    assert was_created is True
    mock_http_client.post.assert_awaited_once()
    mock_http_client.get.assert_not_called()


async def test_new_user_posts_correct_budget_id(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.side_effect = [None, _DB_USER]

    await get_or_create_user(_USER_ID)

    _, kwargs = mock_http_client.post.call_args
    assert kwargs["json"]["user_id"] == _USER_ID
    assert kwargs["json"]["budget_id"] == _BUDGET_ID


async def test_new_user_with_cap_enforcement_admitted(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.side_effect = [None, _DB_USER]
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)

    with patch("mlpa.core.utils.env.MLPA_ENFORCE_SIGNIN_CAP", True):
        user, was_created = await get_or_create_user(_USER_ID)

    assert was_created is True
    mock_app_attest_pg.admit_managed_base_identity.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY, capacity_id=USER_CAPACITY_ID
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
    mock_litellm_pg.get_user.side_effect = [None, None]
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)

    with patch("mlpa.core.utils.env.MLPA_ENFORCE_SIGNIN_CAP", True):
        with pytest.raises(HTTPException) as exc:
            await get_or_create_user(_USER_ID)

    assert exc.value.status_code == 500
    mock_app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY, capacity_id=USER_CAPACITY_ID
    )


async def test_new_user_creation_fails_no_slot_to_release(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.side_effect = [None, None]

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
        base_identity=_BASE_IDENTITY, capacity_id=USER_CAPACITY_ID
    )


async def test_guest_service_type_admits_into_guest_capacity(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.side_effect = [None, None]
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)

    with (
        patch("mlpa.core.utils.env.MLPA_ENFORCE_GUEST_CAP", True),
        patch("mlpa.core.utils.env.MLPA_GUEST_SERVICE_TYPES", {"s2s"}),
    ):
        with pytest.raises(HTTPException):
            await get_or_create_user(_GUEST_USER_ID)

    mock_app_attest_pg.admit_managed_base_identity.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY, capacity_id=GUEST_CAPACITY_ID
    )
    mock_app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY, capacity_id=GUEST_CAPACITY_ID
    )


async def test_guest_cap_full_returns_guest_error_code(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.return_value = None
    mock_app_attest_pg.admit_managed_base_identity.return_value = (False, False)

    with (
        patch("mlpa.core.utils.env.MLPA_ENFORCE_GUEST_CAP", True),
        patch("mlpa.core.utils.env.MLPA_GUEST_SERVICE_TYPES", {"s2s"}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_or_create_user(_GUEST_USER_ID)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"error": ERROR_CODE_MAX_GUESTS_REACHED}


async def test_guest_cap_off_skips_admission(
    mock_litellm_pg, mock_app_attest_pg, mock_http_client
):
    mock_litellm_pg.get_user.side_effect = [None, {"user_id": _GUEST_USER_ID}]

    with patch("mlpa.core.utils.env.MLPA_GUEST_SERVICE_TYPES", {"s2s"}):
        await get_or_create_user(_GUEST_USER_ID)

    mock_app_attest_pg.admit_managed_base_identity.assert_not_called()


def test_overlapping_guest_and_user_service_types_rejected(monkeypatch):
    monkeypatch.setenv("MLPA_GUEST_SERVICE_TYPES", '["ai"]')
    monkeypatch.setenv("MLPA_CAPPED_SERVICE_TYPES", '["ai"]')
    with pytest.raises(ValueError, match="overlap"):
        Env()


@pytest.fixture
def otari_backend(mocker):
    """GATEWAY_BACKEND=otari with a fresh known-users cache and no Otari calls allowed."""
    from mlpa.core.services.otari_service import OtariService

    mocker.patch("mlpa.core.utils.USE_OTARI", True)
    otari = OtariService()
    otari.get_user = AsyncMock(side_effect=AssertionError("no Otari lookup"))
    mocker.patch("mlpa.core.utils.litellm_pg", otari)
    return otari


async def test_otari_admits_a_capped_user_without_asking_otari(
    otari_backend, mock_app_attest_pg, mocker
):
    mocker.patch.object(env, "MLPA_ENFORCE_SIGNIN_CAP", True)
    mock_app_attest_pg.admit_managed_base_identity.return_value = (True, True)

    user, newly_claimed = await get_or_create_user(_USER_ID)

    assert user == {"user_id": _USER_ID, "blocked": False}
    assert newly_claimed is True
    mock_app_attest_pg.admit_managed_base_identity.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY, capacity_id=USER_CAPACITY_ID
    )
    assert otari_backend.is_known(_USER_ID)


async def test_otari_refuses_a_new_user_over_the_cap(
    otari_backend, mock_app_attest_pg, mocker
):
    mocker.patch.object(env, "MLPA_ENFORCE_SIGNIN_CAP", True)
    mock_app_attest_pg.admit_managed_base_identity.return_value = (False, False)

    with pytest.raises(HTTPException) as exc_info:
        await get_or_create_user(_USER_ID)

    assert exc_info.value.status_code == 403
    assert not otari_backend.is_known(_USER_ID)


async def test_otari_skips_the_cap_for_a_known_user(
    otari_backend, mock_app_attest_pg, mocker
):
    mocker.patch.object(env, "MLPA_ENFORCE_SIGNIN_CAP", True)
    otari_backend.remember(_USER_ID)

    user, newly_claimed = await get_or_create_user(_USER_ID)

    assert user["user_id"] == _USER_ID
    assert newly_claimed is False
    mock_app_attest_pg.admit_managed_base_identity.assert_not_awaited()


async def test_otari_refuses_a_new_guest_over_the_guest_cap(
    otari_backend, mock_app_attest_pg, mocker
):
    mocker.patch.object(env, "MLPA_ENFORCE_GUEST_CAP", True)
    mocker.patch.object(env, "MLPA_GUEST_SERVICE_TYPES", {"s2s"})
    mock_app_attest_pg.admit_managed_base_identity.return_value = (False, False)

    with pytest.raises(HTTPException) as exc_info:
        await get_or_create_user(_GUEST_USER_ID)

    assert exc_info.value.detail == {"error": ERROR_CODE_MAX_GUESTS_REACHED}
    mock_app_attest_pg.admit_managed_base_identity.assert_awaited_once_with(
        base_identity=_BASE_IDENTITY, capacity_id=GUEST_CAPACITY_ID
    )
    assert not otari_backend.is_known(_GUEST_USER_ID)
