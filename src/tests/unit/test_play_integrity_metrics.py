import hashlib

import pytest
from fastapi import HTTPException

from mlpa.core.classes import PlayIntegrityRequest
from mlpa.core.config import env
from mlpa.core.prometheus_metrics import PrometheusResult
from mlpa.core.routers.play import play as play_module
from mlpa.core.utils import (
    extract_user_from_play_integrity_jwt,
    issue_mlpa_access_token,
)


def _mock_decode_payload(request_hash: str) -> dict:
    return {
        "tokenPayloadExternal": {
            "requestDetails": {
                "requestPackageName": env.PLAY_INTEGRITY_PACKAGE_NAME,
                "requestHash": request_hash,
            },
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
        }
    }


async def test_verify_play_integrity_records_metrics(mocker):
    request_hash = hashlib.sha256(b"user-id").hexdigest()
    mocker.patch.object(
        play_module,
        "_decode_integrity_token",
        return_value=_mock_decode_payload(request_hash),
    )
    mock_metrics = mocker.patch.object(play_module, "metrics")

    payload = PlayIntegrityRequest(
        integrity_token="test-token",
        user_id="user-id",
        package_name=env.PLAY_INTEGRITY_PACKAGE_NAME,
    )

    await play_module.verify_play_integrity(payload)

    mock_metrics.play_verifications_total.inc.assert_called_once()
    mock_metrics.validate_play_latency.labels.assert_called_once_with(
        result=PrometheusResult.SUCCESS
    )
    mock_metrics.validate_play_latency.labels().observe.assert_called_once()


def test_extract_user_from_play_integrity_jwt_records_success_metrics(mocker):
    mock_metrics = mocker.patch("mlpa.core.utils.metrics")
    token = issue_mlpa_access_token("user-id")

    user_id = extract_user_from_play_integrity_jwt(f"Bearer {token}")

    assert user_id == "user-id"
    mock_metrics.access_token_verifications_total.inc.assert_called_once()
    mock_metrics.validate_access_token_latency.labels.assert_called_once_with(
        result=PrometheusResult.SUCCESS
    )
    mock_metrics.validate_access_token_latency.labels().observe.assert_called_once()


def test_extract_user_from_play_integrity_jwt_records_error_metrics(mocker):
    mock_metrics = mocker.patch("mlpa.core.utils.metrics")

    with pytest.raises(HTTPException):
        extract_user_from_play_integrity_jwt("Bearer invalid-token")

    mock_metrics.access_token_verifications_total.inc.assert_not_called()
    mock_metrics.validate_access_token_latency.labels.assert_called_once_with(
        result=PrometheusResult.ERROR
    )
    mock_metrics.validate_access_token_latency.labels().observe.assert_called_once()
