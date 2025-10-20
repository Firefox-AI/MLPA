import base64

from tests.consts import SUCCESSFUL_CHAT_RESPONSE, TEST_KEY_ID


def test_get_challenge(mocked_client_integration):
	response = mocked_client_integration.get(
		"/verify/challenge",
		params={
			"key_id": TEST_KEY_ID,
		},
	)
	assert response.json().get("challenge") is not None
	assert len(response.json().get("challenge")) > 0


def test_invalid_methods(mocked_client_integration):
	response = mocked_client_integration.post(
		"/verify/challenge",
		params={
			"key_id": TEST_KEY_ID,
		},
	)
	assert response.status_code == 405

	response = mocked_client_integration.put(
		"/verify/challenge",
		json={
			"key_id": TEST_KEY_ID,
		},
	)
	assert response.status_code == 405

	response = mocked_client_integration.delete(
		"/verify/challenge",
		params={
			"key_id": TEST_KEY_ID,
		},
	)
	assert response.status_code == 405

	response = mocked_client_integration.get(
		"/verify/attest",
	)
	assert response.status_code == 405

	response = mocked_client_integration.put(
		"/verify/attest",
		json={"key_id": TEST_KEY_ID},
	)
	assert response.status_code == 405


def test_invalid_challenge(mocked_client_integration):
	response = mocked_client_integration.post(
		"/verify/attest",
		json={
			"key_id": TEST_KEY_ID,
			"challenge_b64": "VEVTVF9DSEFMTExFTkdFX0JBU0U2NFVSTA==",
			"attestation_obj_b64": r"",
		},
	)
	assert response.json() == {"detail": "Invalid or expired challenge"}


def test_successful_request_with_mocked_app_attest_auth(mocked_client_integration):
	challenge_response = mocked_client_integration.get(
		"/verify/challenge", params={"key_id": TEST_KEY_ID}
	)

	challenge = challenge_response.json().get("challenge")
	challenge_b64 = base64.b64encode(challenge.encode()).decode()

	response = mocked_client_integration.post(
		"/v1/chat/completions",
		json={
			"key_id": TEST_KEY_ID,
			"challenge_b64": challenge_b64,
			"assertion_obj_b64": "VEVTVF9BU1NFUlRJT05fQkFTRTY0VVJM",
		},
	)
	assert response.status_code != 401
	assert response.status_code != 400
	assert response.json() == SUCCESSFUL_CHAT_RESPONSE
