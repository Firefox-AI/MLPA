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


async def test_verify_play_integrity_records_metrics(mocker, metrics_spy):
    request_hash = hashlib.sha256(b"user-id").hexdigest()
    mocker.patch.object(
        play_module,
        "_decode_integrity_token",
        return_value=_mock_decode_payload(request_hash),
    )

    payload = PlayIntegrityRequest(
        integrity_token="test-token",
        user_id="user-id",
        package_name=env.PLAY_INTEGRITY_PACKAGE_NAME,
    )

    await play_module.verify_play_integrity(payload)

    metrics_spy.assert_only({"play_verifications_total", "validate_play_latency"})
    assert metrics_spy.value("play_verifications_total") == 1
    assert (
        metrics_spy.histogram_count(
            "validate_play_latency", result=PrometheusResult.SUCCESS
        )
        == 1
    )


def test_extract_user_from_play_integrity_jwt_records_success_metrics(metrics_spy):
    token = issue_mlpa_access_token("user-id")

    user_id = extract_user_from_play_integrity_jwt(f"Bearer {token}")

    assert user_id == "user-id"
    metrics_spy.assert_only(
        {"access_token_verifications_total", "validate_access_token_latency"}
    )
    assert metrics_spy.value("access_token_verifications_total") == 1
    assert (
        metrics_spy.histogram_count(
            "validate_access_token_latency", result=PrometheusResult.SUCCESS
        )
        == 1
    )


def test_extract_user_from_play_integrity_jwt_records_error_metrics(metrics_spy):
    with pytest.raises(HTTPException):
        extract_user_from_play_integrity_jwt("Bearer invalid-token")

    metrics_spy.assert_only({"validate_access_token_latency"})
    assert metrics_spy.value("access_token_verifications_total") == 0
    assert (
        metrics_spy.histogram_count(
            "validate_access_token_latency", result=PrometheusResult.ERROR
        )
        == 1
    )
