from unittest.mock import AsyncMock, MagicMock

from mlpa.core.routers.play import play as play_module


async def test_valid_credentials_skip_threadpool(mocker):
    credentials = MagicMock(valid=True, token="cached-token")
    mocker.patch.object(
        play_module, "_get_service_account_credentials", return_value=credentials
    )
    run_in_threadpool_mock = mocker.patch.object(
        play_module, "run_in_threadpool", new=AsyncMock()
    )

    token = await play_module._get_play_integrity_access_token()

    assert token == "cached-token"
    credentials.refresh.assert_not_called()
    run_in_threadpool_mock.assert_not_called()


async def test_expired_credentials_refresh_via_threadpool(mocker):
    credentials = MagicMock(valid=False)
    credentials.refresh.side_effect = lambda _request: setattr(
        credentials, "token", "refreshed-token"
    )
    mocker.patch.object(
        play_module, "_get_service_account_credentials", return_value=credentials
    )
    run_in_threadpool_mock = mocker.patch.object(
        play_module,
        "run_in_threadpool",
        new=AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)),
    )

    token = await play_module._get_play_integrity_access_token()

    assert token == "refreshed-token"
    run_in_threadpool_mock.assert_called_once()
    assert run_in_threadpool_mock.call_args.args[0] == credentials.refresh
    credentials.refresh.assert_called_once()
